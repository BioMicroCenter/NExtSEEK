# Species and Library-Fact Inference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pipeline agent's exact-string species vote with a deterministic evidence extractor that resolves 92.1% of cohorts instead of 55.9%, and asks rather than guessing for the rest.

**Architecture:** Three new pure-Python modules under `chat_nextseek/src/chat_nextseek/inference/` — a normaliser, a `Fact`/`FactSet` vocabulary, and extraction rules over the lineage flatten. `tool_resolve_samples` builds a `FactSet` and stores it in session state; `tool_configure_run` consumes it, applying facts that qualify and routing the rest into the elicitation channel `user_params.py` already owns. No new module in `seqera/` — the consumers are existing functions.

**Tech Stack:** Python 3.14, `uv`, pytest. No new dependencies.

## Global Constraints

- Design doc: `docs/2026-08-07-pipeline-param-inference-design.md`. Read it before starting.
- All commands run from `chat_nextseek/` (the vendored subpackage has its own `uv` project), **not** the repo root.
- Full suite: `uv run pytest tests/ --ignore=tests/evaluator -q`. The `tests/evaluator/` subdir needs the Django stack and is excluded by default.
- Tests live flat in `chat_nextseek/tests/`, named `test_*.py`.
- Conventional commits with module scopes: `feat(inference): …`, `fix(pipeline): …`.
- **Rules are total functions.** They return facts or nothing; they never raise. A blank, absent or sentinel value produces *no fact*, never a default one.
- **Never widen `species_to_bundle` to paper over a normalisation gap.** The table stays as-is; matching goes through the normaliser.
- Line numbers in the design doc have drifted. Anchor edits on function names and exact strings, not line numbers.

---

### Task 1: The normaliser

The highest-value component. 545 D.SEQ samples (26.5% of the corpus) record `Species = "Macaca mulatta (Rhesus)"` and resolve to no genome bundle purely because of the parenthetical, while 92 samples recording `"Macaca mulatta"` resolve fine.

**Files:**
- Create: `chat_nextseek/src/chat_nextseek/inference/__init__.py`
- Create: `chat_nextseek/src/chat_nextseek/inference/normalise.py`
- Test: `chat_nextseek/tests/test_inference_normalise.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalise(value: Any, fact: str | None = None) -> str`

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_inference_normalise.py`:

```python
import pytest

from chat_nextseek.inference.normalise import normalise


@pytest.mark.parametrize("raw,expected", [
    ("Macaca mulatta (Rhesus)", "macaca mulatta"),   # 545 samples hinge on this
    ("Macaca mulatta", "macaca mulatta"),
    ("Homo Sapiens", "homo sapiens"),
    ("  MOUSE  ", "mouse"),
    ("Mus musculus [C57BL/6J]", "mus musculus"),
    ("Macaca  mulatta", "macaca mulatta"),           # collapsed whitespace
])
def test_normalise_generic(raw, expected):
    assert normalise(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "   ", "N/A", "n/a", "none", "NULL", "unknown", "-"])
def test_sentinels_normalise_to_empty(raw):
    assert normalise(raw) == ""


@pytest.mark.parametrize("raw,expected", [
    ("RNA-Seq", "rna-seq"),
    ("RNA-seq", "rna-seq"),
    ("RNAseq", "rna-seq"),
    ("scRNA-Seq", "scrna-seq"),
    ("Amplicon", "amplicon"),
    ("AMPLICON", "amplicon"),
    ("WGS", "wgs"),
])
def test_normalise_strategy_synonyms(raw, expected):
    assert normalise(raw, fact="strategy") == expected


@pytest.mark.parametrize("raw,expected", [
    ("Paired End", "paired"),
    ("Paired end", "paired"),
    ("PAIRED", "paired"),
    ("paired", "paired"),
    ("Single End", "single"),
])
def test_normalise_layout_synonyms(raw, expected):
    assert normalise(raw, fact="layout") == expected


@pytest.mark.parametrize("raw,expected", [
    ("total RNA", "rna"),
    ("polyA RNA", "rna"),
    ("DNA", "dna"),
])
def test_normalise_molecule_synonyms(raw, expected):
    assert normalise(raw, fact="molecule") == expected


def test_unmapped_value_passes_through_normalised_not_dropped():
    # An unknown strategy must survive so it becomes a question, not silence.
    assert normalise("Ribo-Seq", fact="strategy") == "ribo-seq"


def test_unknown_fact_name_falls_back_to_generic():
    assert normalise("Paired End", fact="not_a_fact") == "paired end"


def test_non_string_values_are_coerced():
    assert normalise(150) == "150"
    assert normalise(True) == "true"
```

- [ ] **Step 2: Run test to verify it fails**

Run from `chat_nextseek/`:
```bash
uv run pytest tests/test_inference_normalise.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'chat_nextseek.inference'`

- [ ] **Step 3: Create the package marker**

Create `chat_nextseek/src/chat_nextseek/inference/__init__.py`:

```python
"""Deterministic inference of run-relevant facts from NExtSEEK sample metadata.

Design: docs/2026-08-07-pipeline-param-inference-design.md
"""
```

- [ ] **Step 4: Write the implementation**

Create `chat_nextseek/src/chat_nextseek/inference/normalise.py`:

```python
"""Normalise free-text metadata values before matching them.

These fields are free text, not a controlled vocabulary. Matching them exactly
is the mistake this replaces: 545 D.SEQ samples (26.5% of the corpus) record
Species = "Macaca mulatta (Rhesus)" and resolve to no genome bundle purely
because of the parenthetical, while 92 recording "Macaca mulatta" resolve fine.
Stripping parentheticals is the difference between 65.6% and 92.1% bundle
resolution (scripts/audit_metadata_coverage.py, 2026-08-07).

Deliberately NOT a species synonym map: reference_bundles.json's
species_to_bundle already carries both "macaca mulatta" and "rhesus", so
case-folding plus parenthetical stripping is enough for species. Synonyms are
only needed where the corpus spells one concept several ways.
"""
from __future__ import annotations

import re
from typing import Any

# Trailing or embedded annotations: "(Rhesus)", "[C57BL/6J]".
_ANNOTATION = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_WHITESPACE = re.compile(r"\s+")

# Present, but meaning "nothing recorded". Must yield "" so that absence of
# evidence stays distinguishable from evidence of absence.
_SENTINELS = {"n/a", "na", "none", "null", "-", "unknown", "0"}

# Per-fact spelling collapse, applied after the generic pass. Keys are already
# generically normalised.
_SYNONYMS: dict[str, dict[str, str]] = {
    "strategy": {
        "rnaseq": "rna-seq",
        "rna seq": "rna-seq",
        "scrnaseq": "scrna-seq",
        "scrna seq": "scrna-seq",
        "single cell rna-seq": "scrna-seq",
    },
    "layout": {
        "paired end": "paired",
        "pairedend": "paired",
        "single end": "single",
        "singleend": "single",
    },
    "molecule": {
        "total rna": "rna",
        "polya rna": "rna",
        "mrna": "rna",
        "genomic dna": "dna",
    },
}


def normalise(value: Any, fact: str | None = None) -> str:
    """Case-fold, strip annotations and collapse whitespace; then apply the
    per-fact synonym map when ``fact`` names one.

    Returns "" for anything blank or sentinel. An unmapped value is returned
    normalised rather than dropped, so it fails a downstream lookup and becomes
    a question instead of silence.
    """
    if value is None or isinstance(value, (list, dict, set, tuple)):
        return ""
    text = str(value).strip().lower()
    if not text or text in _SENTINELS:
        return ""
    text = _ANNOTATION.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text or text in _SENTINELS:
        return ""
    return _SYNONYMS.get(fact or "", {}).get(text, text)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_inference_normalise.py -q
```
Expected: `24 passed`

- [ ] **Step 6: Commit**

```bash
git add src/chat_nextseek/inference/__init__.py src/chat_nextseek/inference/normalise.py tests/test_inference_normalise.py
git commit -m "feat(inference): normalise free-text metadata before matching

545 D.SEQ samples record Species = 'Macaca mulatta (Rhesus)' and resolve to no
genome bundle purely on the parenthetical. Case-folding alone does not fix it."
```

---

### Task 2: Fact and FactSet

**Files:**
- Create: `chat_nextseek/src/chat_nextseek/inference/facts.py`
- Test: `chat_nextseek/tests/test_inference_facts.py`

**Interfaces:**
- Consumes: nothing from Task 1 (facts carry already-normalised values).
- Produces:
  - `Fact(name, value, source, strength, uids=(), partial=False)` with `.applies() -> bool`
  - `FactSet()` with `.add(Fact)`, `.add_all(Iterable[Fact])`, `.best(name) -> Fact | None`, `.values(name) -> dict[str, list[str]]`, `.is_conflicted(name) -> bool`, `.questions() -> list[dict]`, `.to_dict() -> dict`, `FactSet.from_dict(dict) -> FactSet`
  - `STRENGTHS = ("exact", "strong", "weak")`

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_inference_facts.py`:

```python
from chat_nextseek.inference.facts import Fact, FactSet


def test_exact_and_strong_apply_weak_does_not():
    assert Fact("species", "mouse", "sample_type:MUS", "exact").applies() is True
    assert Fact("species", "mouse", "MUS.Species", "strong").applies() is True
    assert Fact("species", "mouse", "MUS.Strain", "weak").applies() is False


def test_partial_never_applies_whatever_the_strength():
    fact = Fact("species", "macaque", "sample_type:NHP", "exact", partial=True)
    assert fact.applies() is False


def test_best_returns_the_highest_strength_fact():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "MUS.Strain", "weak", ("A",)))
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A",)))
    best = fs.best("species")
    assert best.strength == "exact"
    assert best.source == "sample_type:MUS"


def test_best_returns_none_for_an_unknown_fact_name():
    assert FactSet().best("species") is None


def test_values_groups_uids_by_value():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A", "B")))
    fs.add(Fact("species", "human", "CEL.Species", "strong", ("C",)))
    assert fs.values("species") == {"mouse": ["A", "B"], "human": ["C"]}


def test_conflict_is_more_than_one_distinct_applying_value():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A",)))
    fs.add(Fact("species", "human", "CEL.Species", "strong", ("B",)))
    assert fs.is_conflicted("species") is True


def test_agreement_across_many_samples_is_not_a_conflict():
    fs = FactSet()
    for uid in ("A", "B", "C"):
        fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", (uid,)))
    assert fs.is_conflicted("species") is False
    assert fs.best("species").value == "mouse"


def test_a_weak_fact_does_not_create_a_conflict_with_an_applying_one():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A",)))
    fs.add(Fact("species", "rat", "MUS.Strain", "weak", ("B",)))
    assert fs.is_conflicted("species") is False


def test_questions_reports_conflict_with_the_split():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A", "B")))
    fs.add(Fact("species", "human", "CEL.Species", "strong", ("C",)))
    q = [q for q in fs.questions() if q["name"] == "species"]
    assert len(q) == 1
    assert q[0]["reason"] == "conflict"
    assert q[0]["values"] == {"mouse": ["A", "B"], "human": ["C"]}


def test_questions_reports_partial():
    fs = FactSet()
    fs.add(Fact("species", "macaque", "sample_type:NHP", "exact", ("A",), partial=True))
    q = fs.questions()
    assert q[0]["reason"] == "partial"
    assert q[0]["suggestion"] == "macaque"


def test_questions_reports_weak_with_the_weak_reading_as_suggestion():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "MUS.Strain", "weak", ("A",)))
    q = fs.questions()
    assert q[0]["reason"] == "weak"
    assert q[0]["suggestion"] == "mouse"


def test_questions_is_empty_when_everything_applies_and_agrees():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A",)))
    assert fs.questions() == []


def test_round_trips_through_dict():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A", "B")))
    fs.add(Fact("strategy", "rna-seq", "D.SEQ.LibraryStrategy", "exact", ("A",)))
    restored = FactSet.from_dict(fs.to_dict())
    assert restored.best("species").value == "mouse"
    assert restored.best("strategy").source == "D.SEQ.LibraryStrategy"


def test_to_dict_shape_is_the_documented_state_payload():
    fs = FactSet()
    fs.add(Fact("species", "mouse", "sample_type:MUS", "exact", ("A",)))
    assert fs.to_dict()["species"] == {
        "value": "mouse", "strength": "exact",
        "source": "sample_type:MUS", "uids": ["A"], "partial": False,
    }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_inference_facts.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'chat_nextseek.inference.facts'`

- [ ] **Step 3: Write the implementation**

Create `chat_nextseek/src/chat_nextseek/inference/facts.py`:

```python
"""The fact vocabulary: what was concluded, from where, and how good the evidence is.

Knows nothing about nf-core or pipelines. Consumers decide what a fact means
for a run; this module only decides whether the evidence is good enough to act
on and, when it is not, what to ask about.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

STRENGTHS = ("exact", "strong", "weak")
_ORDER = {"exact": 3, "strong": 2, "weak": 1}
_APPLY_THRESHOLD = 2  # exact and strong apply; weak asks.


@dataclass(frozen=True)
class Fact:
    """One conclusion about one cohort, from one sample's evidence.

    ``partial`` is not a rung on the strength ladder. It means the evidence is
    sound but incomplete — sample type NHP establishes macaque but not *which*
    macaque — so the fact never applies whatever strength produced it.
    """
    name: str
    value: str
    source: str
    strength: str
    uids: tuple[str, ...] = ()
    partial: bool = False

    def applies(self) -> bool:
        return not self.partial and _ORDER.get(self.strength, 0) >= _APPLY_THRESHOLD


@dataclass
class FactSet:
    """Facts aggregated across a cohort's leaves."""
    _by_name: dict[str, list[Fact]] = field(default_factory=dict)

    def add(self, fact: Fact | None) -> None:
        if fact is not None:
            self._by_name.setdefault(fact.name, []).append(fact)

    def add_all(self, facts: Iterable[Fact | None]) -> None:
        for fact in facts or ():
            self.add(fact)

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def best(self, name: str) -> Fact | None:
        """Highest-strength fact for ``name``, merging the uids that agree with it."""
        facts = self._by_name.get(name) or []
        if not facts:
            return None
        top = max(facts, key=lambda f: (_ORDER.get(f.strength, 0), not f.partial))
        uids: list[str] = []
        for fact in facts:
            if fact.value == top.value:
                uids += [u for u in fact.uids if u not in uids]
        return Fact(top.name, top.value, top.source, top.strength,
                    tuple(uids), top.partial)

    def values(self, name: str) -> dict[str, list[str]]:
        """value -> supporting uids, insertion-ordered, for showing a real split."""
        grouped: dict[str, list[str]] = {}
        for fact in self._by_name.get(name) or []:
            bucket = grouped.setdefault(fact.value, [])
            bucket += [u for u in fact.uids if u not in bucket]
        return grouped

    def is_conflicted(self, name: str) -> bool:
        """More than one distinct value among the facts that would apply.

        Weak and partial facts are excluded: a corroborating guess must not be
        able to manufacture a conflict with solid evidence.
        """
        applying = {f.value for f in (self._by_name.get(name) or []) if f.applies()}
        return len(applying) > 1

    def questions(self) -> list[dict[str, Any]]:
        """Facts that cannot be acted on: conflicting, partial, or weak-only.

        A fact name that is simply absent is not reported here — only the
        consumer knows whether it needed that fact.
        """
        out: list[dict[str, Any]] = []
        for name in self.names():
            best = self.best(name)
            if best is None:
                continue
            if self.is_conflicted(name):
                out.append({"name": name, "reason": "conflict",
                            "values": self.values(name), "suggestion": None})
            elif best.partial:
                out.append({"name": name, "reason": "partial",
                            "values": self.values(name), "suggestion": best.value})
            elif not best.applies():
                out.append({"name": name, "reason": "weak",
                            "values": self.values(name), "suggestion": best.value})
        return out

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for name in self.names():
            best = self.best(name)
            if best is None:
                continue
            payload[name] = {
                "value": best.value,
                "strength": best.strength,
                "source": best.source,
                "uids": list(best.uids),
                "partial": best.partial,
            }
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "FactSet":
        restored = cls()
        for name, blob in (payload or {}).items():
            if not isinstance(blob, dict):
                continue
            restored.add(Fact(
                name=name,
                value=str(blob.get("value") or ""),
                source=str(blob.get("source") or ""),
                strength=str(blob.get("strength") or "weak"),
                uids=tuple(blob.get("uids") or ()),
                partial=bool(blob.get("partial")),
            ))
        return restored
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_inference_facts.py -q
```
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add src/chat_nextseek/inference/facts.py tests/test_inference_facts.py
git commit -m "feat(inference): add Fact/FactSet with strength tiers and conflict detection"
```

---

### Task 3: Species rules

**Files:**
- Create: `chat_nextseek/src/chat_nextseek/inference/rules.py`
- Test: `chat_nextseek/tests/test_inference_species_rules.py`

**Interfaces:**
- Consumes: `normalise` (Task 1); `Fact` (Task 2).
- Produces:
  - `ancestor_chain(uid: str, uid_index: dict) -> list[tuple[str, dict]]` — `(sample_type, metadata)` nearest-first, leaf included.
  - `first_populated(chain, *names) -> tuple[str, str, str]` — `(raw_value, sample_type, field_name)`, all `""` when nothing is populated.
  - `species_facts(leaf: dict, uid_index: dict) -> list[Fact]`

The `uid_index` shape is `{uid: {"sample_type": str, "parent_uid": str | None, "metadata": dict}}`, built by `reports/metadata.py::build_metadata_summary` and already passed to `_flatten_lineage`.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_inference_species_rules.py`:

```python
from chat_nextseek.inference.rules import ancestor_chain, species_facts


def index(*entries):
    """entries: (uid, sample_type, parent_uid, metadata)"""
    return {uid: {"sample_type": st, "parent_uid": parent, "metadata": md}
            for uid, st, parent, md in entries}


def leaf(uid, metadata=None):
    return {"uid": uid, "sample_type": "D.SEQ", "metadata": metadata or {}}


def test_ancestor_chain_is_nearest_first_and_includes_the_leaf():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "DNA-1", {"a": 1}),
        ("DNA-1", "DNA", "MUS-1", {"b": 2}),
        ("MUS-1", "MUS", None, {"Strain": "C57BL/6J"}),
    )
    assert [st for st, _ in ancestor_chain("D.SEQ-1", idx)] == ["D.SEQ", "DNA", "MUS"]


def test_ancestor_chain_survives_a_cycle():
    idx = index(("A", "D.SEQ", "B", {}), ("B", "TIS", "A", {}))
    assert [st for st, _ in ancestor_chain("A", idx)] == ["D.SEQ", "TIS"]


def test_ancestor_chain_takes_the_first_parent_of_a_semicolon_list():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "MUS-1; MUS-2", {}),
        ("MUS-1", "MUS", None, {}),
        ("MUS-2", "MUS", None, {}),
    )
    assert [st for st, _ in ancestor_chain("D.SEQ-1", idx)] == ["D.SEQ", "MUS"]


def test_mus_sample_type_alone_yields_an_exact_mouse_fact():
    # MUS declares no Species attribute at all -- the sample type IS the evidence.
    idx = index(
        ("D.SEQ-1", "D.SEQ", "MUS-1", {}),
        ("MUS-1", "MUS", None, {"Strain": "C57BL/6J"}),
    )
    fact = species_facts(leaf("D.SEQ-1"), idx)[0]
    assert (fact.value, fact.strength, fact.partial) == ("mouse", "exact", False)
    assert fact.source == "sample_type:MUS"
    assert fact.uids == ("D.SEQ-1",)


def test_species_field_with_a_parenthetical_still_resolves():
    # 545 samples in the live corpus look exactly like this.
    idx = index(
        ("D.SEQ-1", "D.SEQ", "NHP-1", {}),
        ("NHP-1", "NHP", None, {"Species": "Macaca mulatta (Rhesus)"}),
    )
    fact = species_facts(leaf("D.SEQ-1"), idx)[0]
    assert fact.value == "macaca mulatta"
    assert fact.strength == "strong"
    assert fact.partial is False


def test_nhp_without_a_species_field_is_partial_not_a_guess():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "NHP-1", {}),
        ("NHP-1", "NHP", None, {"Notes": "arrived 2024"}),
    )
    fact = species_facts(leaf("D.SEQ-1"), idx)[0]
    assert fact.partial is True
    assert fact.applies() is False
    assert fact.source == "sample_type:NHP"


def test_taxonomy_id_wins_over_everything_else():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "CEL-1", {}),
        ("CEL-1", "CEL", None, {"TaxonomyID": "9606", "Species": "something else"}),
    )
    fact = species_facts(leaf("D.SEQ-1"), idx)[0]
    assert (fact.value, fact.strength) == ("human", "exact")
    assert fact.source == "CEL.TaxonomyID"


def test_strain_only_is_weak_and_does_not_apply():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "CEL-1", {}),
        ("CEL-1", "CEL", None, {"Strain": "BALB/c"}),
    )
    fact = species_facts(leaf("D.SEQ-1"), idx)[0]
    assert (fact.value, fact.strength) == ("mouse", "weak")
    assert fact.applies() is False


def test_no_evidence_yields_no_fact():
    idx = index(("D.SEQ-1", "D.SEQ", None, {"Sequencer": "Illumina MiSeq"}))
    assert species_facts(leaf("D.SEQ-1"), idx) == []


def test_sentinel_species_value_yields_no_fact():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "CEL-1", {}),
        ("CEL-1", "CEL", None, {"Species": "unknown"}),
    )
    assert species_facts(leaf("D.SEQ-1"), idx) == []


def test_falls_back_to_leaf_metadata_when_the_index_is_empty():
    # build_metadata_summary can fail; resolve_samples continues with {}.
    result = species_facts(leaf("D.SEQ-1", {"Species": "Homo sapiens"}), {})
    assert result[0].value == "homo sapiens"


def test_nearest_ancestor_wins_over_a_more_distant_one():
    idx = index(
        ("D.SEQ-1", "D.SEQ", "CEL-1", {}),
        ("CEL-1", "CEL", "MUS-1", {"Species": "Homo sapiens"}),
        ("MUS-1", "MUS", None, {}),
    )
    assert species_facts(leaf("D.SEQ-1"), idx)[0].value == "homo sapiens"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_inference_species_rules.py -q
```
Expected: collection error — `ModuleNotFoundError: No module named 'chat_nextseek.inference.rules'`

- [ ] **Step 3: Write the implementation**

Create `chat_nextseek/src/chat_nextseek/inference/rules.py`:

```python
"""Extraction rules: flattened lineage in, typed facts out.

Every rule is a total function -- it returns facts or nothing, and never
raises. A blank or sentinel field produces NO fact, never a default one:
absence of evidence has to stay distinguishable from evidence of absence, or
the strength tiering collapses.
"""
from __future__ import annotations

from typing import Any

from .facts import Fact
from .normalise import normalise

# NCBI taxids for the species the reference store can actually serve.
# Populated on 0 samples today (audit, 2026-08-07); kept because it is three
# lines and is the only unambiguous key available if curation adds it.
_TAXONOMY = {
    "9606": "human",
    "10090": "mouse",
    "9544": "macaca mulatta",
    "9541": "macaca fascicularis",
}

# Sample type -> what the type alone proves. `partial` means the type narrows
# the answer without settling it: NHP is a macaque, but Mmul_10 and Mfas6.0 are
# different genomes.
_TYPE_SPECIES: dict[str, tuple[str, bool]] = {
    "MUS": ("mouse", False),
    "NHP": ("macaque", True),
}

# Strain/genotype tokens that corroborate a species without establishing it.
_STRAIN_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mouse", ("c57bl", "balb/c", "balb c", "129s", "dba/2", "fvb", "nod scid")),
)


def ancestor_chain(uid: str, uid_index: dict) -> list[tuple[str, dict]]:
    """``(sample_type, metadata)`` nearest-first, starting with the leaf itself.

    Follows the same ``parent_uid`` links as ``_flatten_lineage``, but keeps the
    sample type of each hop -- which is the whole point, because MUS carries the
    species in its type rather than in a field.
    """
    chain: list[tuple[str, dict]] = []
    seen: set[str] = set()
    cur: str | None = uid
    while cur and cur in uid_index and cur not in seen:
        seen.add(cur)
        entry = uid_index[cur] or {}
        chain.append((str(entry.get("sample_type") or ""), entry.get("metadata") or {}))
        parent = entry.get("parent_uid")
        # Parent can be a semicolon-separated list; follow the first that resolves.
        cur = None
        for candidate in str(parent or "").split(";"):
            candidate = candidate.strip()
            if candidate and candidate in uid_index and candidate not in seen:
                cur = candidate
                break
    return chain


def _chain_for(leaf: dict, uid_index: dict) -> list[tuple[str, dict]]:
    """The lineage chain, or the leaf's own metadata when the index is unusable."""
    chain = ancestor_chain(str(leaf.get("uid") or ""), uid_index) if uid_index else []
    if chain:
        return chain
    return [(str(leaf.get("sample_type") or ""), leaf.get("metadata") or {})]


def first_populated(chain: list[tuple[str, dict]], *names: str) -> tuple[str, str, str]:
    """First real value of any of ``names``, nearest ancestor first.

    Returns ``(raw_value, sample_type, field_name)``; all "" when nothing is
    populated. Sentinels ("N/A", "unknown") count as unpopulated.
    """
    for sample_type, metadata in chain:
        for name in names:
            if normalise((metadata or {}).get(name)):
                return str((metadata or {}).get(name)), sample_type, name
    return "", "", ""


def species_facts(leaf: dict, uid_index: dict) -> list[Fact]:
    """Species evidence for one leaf, strongest rule first.

    Order: TaxonomyID (exact) > sample type (exact, or partial for NHP) >
    Species field (strong) > Strain/Genotype (weak). Returns at most one fact.
    """
    uid = str(leaf.get("uid") or "")
    chain = _chain_for(leaf, uid_index)

    raw, sample_type, _ = first_populated(chain, "TaxonomyID")
    if raw:
        species = _TAXONOMY.get(normalise(raw))
        if species:
            return [Fact("species", species, f"{sample_type}.TaxonomyID", "exact", (uid,))]

    for sample_type, _ in chain:
        known = _TYPE_SPECIES.get(sample_type)
        if known:
            value, partial = known
            if not partial:
                return [Fact("species", value, f"sample_type:{sample_type}",
                             "exact", (uid,))]
            # Partial: the type narrows it, so let a Species field settle it.
            raw, src_type, _ = first_populated(chain, "Species")
            if raw:
                return [Fact("species", normalise(raw), f"{src_type}.Species",
                             "strong", (uid,))]
            return [Fact("species", value, f"sample_type:{sample_type}",
                         "exact", (uid,), partial=True)]

    raw, sample_type, _ = first_populated(chain, "Species")
    if raw:
        return [Fact("species", normalise(raw), f"{sample_type}.Species",
                     "strong", (uid,))]

    raw, sample_type, field = first_populated(chain, "Strain", "Genotype")
    if raw:
        text = normalise(raw)
        for species, hints in _STRAIN_HINTS:
            if any(hint in text for hint in hints):
                return [Fact("species", species, f"{sample_type}.{field}",
                             "weak", (uid,))]
    return []
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_inference_species_rules.py -q
```
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add src/chat_nextseek/inference/rules.py tests/test_inference_species_rules.py
git commit -m "feat(inference): species rules over the lineage chain

MUS carries the species in its sample type rather than a field, which is why
the current exact-string vote resolves 0% of mouse cohorts."
```

---

### Task 4: Library-fact rules

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/inference/rules.py` (append)
- Test: `chat_nextseek/tests/test_inference_library_rules.py`

**Interfaces:**
- Consumes: `first_populated`, `_chain_for`, `Fact`, `normalise` (Tasks 1–3).
- Produces:
  - `platform_from_text(blob: str) -> str` — shared with the emitter in Task 9.
  - `library_facts(leaf: dict, uid_index: dict) -> list[Fact]`
  - `extract_facts(leaf: dict, uid_index: dict) -> list[Fact]` — species + library, the single entry point `tool_resolve_samples` calls.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_inference_library_rules.py`:

```python
import pytest

from chat_nextseek.inference.rules import extract_facts, library_facts, platform_from_text


def index(uid, metadata):
    return {uid: {"sample_type": "D.SEQ", "parent_uid": None, "metadata": metadata}}


def leaf(uid="D.SEQ-1"):
    return {"uid": uid, "sample_type": "D.SEQ", "metadata": {}}


def facts_by_name(metadata):
    return {f.name: f for f in library_facts(leaf(), index("D.SEQ-1", metadata))}


@pytest.mark.parametrize("blob,expected", [
    ("Illumina MiSeq", "illumina"),
    ("Illumina NovaSeq 6000", "illumina"),
    ("NovaSeq 6000, Illumina", "illumina"),
    ("Singular G4", "illumina"),
    ("PacBio Revio", "pacbio"),
    ("Oxford Nanopore PromethION", "nanopore"),
    ("ONT", "nanopore"),          # the hint that is dead in emitter.py today
    ("ont MinION", "nanopore"),
    ("", ""),
    ("mystery machine", ""),
])
def test_platform_from_text(blob, expected):
    assert platform_from_text(blob) == expected


def test_specific_instrument_families_beat_the_vendor_word():
    assert platform_from_text("Illumina-prepped PacBio Sequel run") == "pacbio"


def test_strategy_is_exact_and_normalised():
    fact = facts_by_name({"LibraryStrategy": "RNA-Seq"})["strategy"]
    assert (fact.value, fact.strength) == ("rna-seq", "exact")
    assert fact.source == "D.SEQ.LibraryStrategy"


@pytest.mark.parametrize("raw,expected", [
    ("Amplicon", "amplicon"), ("AMPLICON", "amplicon"),
    ("RNA-seq", "rna-seq"), ("RNAseq", "rna-seq"), ("scRNA-Seq", "scrna-seq"),
])
def test_strategy_spelling_variants_collapse(raw, expected):
    assert facts_by_name({"LibraryStrategy": raw})["strategy"].value == expected


def test_layout_comes_from_library_design_not_sequencing_type():
    # SequencingType holds modality ("Illumina Sequencing"), never layout.
    result = facts_by_name({"LibraryDesign": "Paired End",
                            "SequencingType": "Illumina Sequencing"})
    assert result["layout"].value == "paired"
    assert result["layout"].source == "D.SEQ.LibraryDesign"


def test_molecule_reads_both_schema_spellings():
    # D.SEQ declares ExractedMolecule (Standard) and ExtractedMolecule (Possible).
    assert facts_by_name({"ExractedMolecule": "total RNA"})["molecule"].value == "rna"
    assert facts_by_name({"ExtractedMolecule": "DNA"})["molecule"].value == "dna"


def test_blank_fields_yield_no_facts():
    assert library_facts(leaf(), index("D.SEQ-1", {"LibraryStrategy": "  "})) == []


def test_sentinel_fields_yield_no_facts():
    assert library_facts(leaf(), index("D.SEQ-1", {"Sequencer": "N/A"})) == []


def test_unrecognised_sequencer_yields_no_platform_fact():
    assert "platform" not in facts_by_name({"Sequencer": "Homebrew Mk1"})


def test_extract_facts_returns_species_and_library_together():
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "MUS-1",
                    "metadata": {"LibraryStrategy": "RNA-Seq"}},
        "MUS-1": {"sample_type": "MUS", "parent_uid": None, "metadata": {}},
    }
    names = {f.name for f in extract_facts(leaf(), idx)}
    assert {"species", "strategy"} <= names


def test_rules_never_raise_on_malformed_metadata():
    idx = {"D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": None,
                       "metadata": {"LibraryStrategy": ["a", "list"],
                                    "Sequencer": {"a": "dict"}}}}
    assert extract_facts(leaf(), idx) == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_inference_library_rules.py -q
```
Expected: `ImportError: cannot import name 'library_facts'`

- [ ] **Step 3: Append the implementation**

Append to `chat_nextseek/src/chat_nextseek/inference/rules.py`:

```python
# Order matters: specific instrument families before the vendor words, so a
# record naming both PacBio and Illumina resolves to pacbio.
_PLATFORM_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pacbio", ("pacbio", "hifi", "revio", "sequel")),
    ("nanopore", ("nanopore", "minion", "promethion", "gridion", "ont")),
    ("illumina", ("illumina", "novaseq", "miseq", "nextseq", "hiseq", "iseq",
                  "singular")),
)

# Fact name -> (field names to try, strength). Sources are D.SEQ standard
# metadata; population rates from the 2026-08-07 audit are in the design doc.
_LIBRARY_FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("strategy", ("LibraryStrategy",), "exact"),          # 100% populated
    ("layout", ("LibraryDesign",), "exact"),              # 99.9% populated
    ("molecule", ("ExtractedMolecule", "ExractedMolecule"), "strong"),  # 65.6%
)


def platform_from_text(blob: str) -> str:
    """Sequencing platform family from free text, or "".

    Word-boundary safe for the short tokens: "ont" must not match "front".
    """
    text = normalise(blob)
    if not text:
        return ""
    padded = f" {text} "
    for platform, hints in _PLATFORM_HINTS:
        for hint in hints:
            if len(hint) <= 3:
                if f" {hint} " in padded:
                    return platform
            elif hint in text:
                return platform
    return ""


def library_facts(leaf: dict, uid_index: dict) -> list[Fact]:
    """Library-level facts for one leaf: strategy, layout, molecule, platform."""
    uid = str(leaf.get("uid") or "")
    chain = _chain_for(leaf, uid_index)
    out: list[Fact] = []

    for name, fields, strength in _LIBRARY_FIELDS:
        raw, sample_type, field = first_populated(chain, *fields)
        value = normalise(raw, fact=name)
        if value:
            out.append(Fact(name, value, f"{sample_type}.{field}", strength, (uid,)))

    raw, sample_type, field = first_populated(chain, "Sequencer")
    platform = platform_from_text(raw)
    if platform:
        out.append(Fact("platform", platform, f"{sample_type}.{field}", "exact", (uid,)))
    return out


def extract_facts(leaf: dict, uid_index: dict) -> list[Fact]:
    """Every fact derivable from one leaf. The entry point resolve_samples calls."""
    return species_facts(leaf, uid_index) + library_facts(leaf, uid_index)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_inference_library_rules.py -q
```
Expected: `26 passed`

- [ ] **Step 5: Run the whole inference suite**

```bash
uv run pytest tests/test_inference_*.py -q
```
Expected: `52 passed`

- [ ] **Step 6: Commit**

```bash
git add src/chat_nextseek/inference/rules.py tests/test_inference_library_rules.py
git commit -m "feat(inference): library-fact rules (strategy, layout, molecule, platform)

Layout comes from LibraryDesign, not SequencingType -- the audit showed
SequencingType holds modality. platform_from_text is word-boundary safe so
'ont' matches ONT without matching 'front'."
```

---

### Task 5: Wire facts into `resolve_samples`

Replaces the species vote. This is the task that moves the number from 55.9% to 92.1%.

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py` (function `tool_resolve_samples`)
- Test: `chat_nextseek/tests/test_resolve_samples_facts.py`

**Interfaces:**
- Consumes: `extract_facts` (Task 4), `FactSet` (Task 2).
- Produces: `state["facts"]` in the `FactSet.to_dict()` shape; `state["detected_species"]` and `state["bundle_key"]` keep their existing meaning; the tool result gains a `"facts"` key.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_resolve_samples_facts.py`:

```python
import json

from chat_nextseek.pipeline import agent_tools


def _patch_metadata(monkeypatch, leaves, uid_index):
    """Stub out the network + lineage walk, leaving the fact logic under test."""
    monkeypatch.setattr(agent_tools, "fetch_reporter_metadata",
                        lambda config, uids: {"ok": True, "data": {}})
    monkeypatch.setattr(agent_tools, "annotate_metadata_with_sampletypes",
                        lambda config, raw: raw)
    monkeypatch.setattr(agent_tools, "enumerate_lineage_leaves",
                        lambda annotated, accepted_types=None: leaves)
    monkeypatch.setattr(agent_tools, "build_metadata_summary",
                        lambda payload: {"_uid_index": uid_index})
    monkeypatch.setattr(agent_tools, "filter_summary_to_sequencing_lineage",
                        lambda summary: summary)


def _run(monkeypatch, leaves, uid_index, state=None):
    _patch_metadata(monkeypatch, leaves, uid_index)
    state = state if state is not None else {}
    raw = agent_tools.tool_resolve_samples(
        object(), {}, state,
        {"kind": "explicit_uids", "uids": [leaf["uid"] for leaf in leaves]},
        "rnaseq")
    return json.loads(raw), state


def test_mouse_cohort_resolves_to_grcm39(monkeypatch):
    """Today this cohort resolves to None and submits against GRCh38."""
    leaves = [{"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
               "source_uid": "MUS-1", "metadata": {}}]
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "MUS-1", "metadata": {}},
        "MUS-1": {"sample_type": "MUS", "parent_uid": None,
                  "metadata": {"Strain": "C57BL/6J"}},
    }
    result, state = _run(monkeypatch, leaves, idx)
    assert state["detected_species"] == "mouse"
    assert state["bundle_key"] == "GRCm39"
    assert result["bundle_key"] == "GRCm39"


def test_parenthetical_species_resolves_to_mmul_10(monkeypatch):
    leaves = [{"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
               "source_uid": "NHP-1", "metadata": {}}]
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "NHP-1", "metadata": {}},
        "NHP-1": {"sample_type": "NHP", "parent_uid": None,
                  "metadata": {"Species": "Macaca mulatta (Rhesus)"}},
    }
    result, state = _run(monkeypatch, leaves, idx)
    assert state["bundle_key"] == "Mmul_10"


def test_facts_are_stored_in_state_with_source_and_strength(monkeypatch):
    leaves = [{"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
               "source_uid": "MUS-1", "metadata": {}}]
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "MUS-1",
                    "metadata": {"LibraryStrategy": "RNA-Seq"}},
        "MUS-1": {"sample_type": "MUS", "parent_uid": None, "metadata": {}},
    }
    result, state = _run(monkeypatch, leaves, idx)
    assert state["facts"]["species"] == {
        "value": "mouse", "strength": "exact", "source": "sample_type:MUS",
        "uids": ["D.SEQ-1"], "partial": False,
    }
    assert state["facts"]["strategy"]["value"] == "rna-seq"
    assert result["facts"]["species"]["source"] == "sample_type:MUS"


def test_partial_evidence_does_not_set_a_bundle(monkeypatch):
    leaves = [{"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
               "source_uid": "NHP-1", "metadata": {}}]
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "NHP-1", "metadata": {}},
        "NHP-1": {"sample_type": "NHP", "parent_uid": None, "metadata": {}},
    }
    result, state = _run(monkeypatch, leaves, idx)
    assert state["detected_species"] is None
    assert state["bundle_key"] is None
    assert state["facts"]["species"]["partial"] is True


def test_conflicting_cohort_sets_no_bundle(monkeypatch):
    leaves = [
        {"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
         "source_uid": "MUS-1", "metadata": {}},
        {"uid": "D.SEQ-2", "sample_type": "D.SEQ", "assay": "",
         "source_uid": "CEL-1", "metadata": {}},
    ]
    idx = {
        "D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": "MUS-1", "metadata": {}},
        "MUS-1": {"sample_type": "MUS", "parent_uid": None, "metadata": {}},
        "D.SEQ-2": {"sample_type": "D.SEQ", "parent_uid": "CEL-1", "metadata": {}},
        "CEL-1": {"sample_type": "CEL", "parent_uid": None,
                  "metadata": {"Species": "Homo sapiens"}},
    }
    result, state = _run(monkeypatch, leaves, idx)
    assert state["bundle_key"] is None


def test_no_evidence_leaves_species_none(monkeypatch):
    leaves = [{"uid": "D.SEQ-1", "sample_type": "D.SEQ", "assay": "",
               "source_uid": "X", "metadata": {}}]
    idx = {"D.SEQ-1": {"sample_type": "D.SEQ", "parent_uid": None,
                       "metadata": {"Sequencer": "Illumina MiSeq"}}}
    result, state = _run(monkeypatch, leaves, idx)
    assert state["detected_species"] is None
    assert state["facts"]["platform"]["value"] == "illumina"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_resolve_samples_facts.py -q
```
Expected: FAIL — `test_mouse_cohort_resolves_to_grcm39` gets `detected_species is None`, and every `state["facts"]` lookup raises `KeyError: 'facts'`.

- [ ] **Step 3: Add the imports**

In `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py`, immediately after the existing `from ..schemas import SeqeraLaunchPlan` line, add:

```python
from ..inference.facts import FactSet
from ..inference.rules import extract_facts
```

- [ ] **Step 4: Remove the species vote**

In `tool_resolve_samples`, delete this declaration (it sits with `all_uids` / `all_accs`):

```python
    species_votes: Counter = Counter()
```

and delete this block from inside the per-leaf loop:

```python
        # Generically detect species: any flattened value that maps to a reference
        # bundle is a species vote (no hardcoded field name).
        for val in flat.values():
            if isinstance(val, str) and resolve_bundle_for_species(val):
                species_votes[val.strip()] += 1
```

- [ ] **Step 5: Build the FactSet**

In `tool_resolve_samples`, add this declaration next to `all_uids`:

```python
    factset = FactSet()
```

and, inside the per-leaf loop, immediately before `leaf_fields = {...}`, add:

```python
        # Typed evidence (species, strategy, layout, molecule, platform) with its
        # source and strength. Replaces the exact-string species vote, which could
        # not match MUS at all (no Species attribute) and lost 545 samples to the
        # "Macaca mulatta (Rhesus)" parenthetical.
        factset.add_all(extract_facts(leaf, uid_index))
```

- [ ] **Step 6: Replace the species conclusion**

Replace:

```python
    detected_species = species_votes.most_common(1)[0][0] if species_votes else None
    bundle_key = resolve_bundle_for_species(detected_species)
```

with:

```python
    # Only evidence that qualifies sets a genome. Partial (NHP with no Species),
    # weak (strain only) and conflicting cohorts leave it None, and configure_run
    # turns that into a question rather than a default.
    species_fact = factset.best("species")
    detected_species = (species_fact.value
                        if species_fact and species_fact.applies()
                        and not factset.is_conflicted("species") else None)
    bundle_key = resolve_bundle_for_species(detected_species)
    state["facts"] = factset.to_dict()
```

- [ ] **Step 7: Surface facts in the tool result**

In the `return json.dumps({...})` at the end of `tool_resolve_samples`, add this entry immediately after `"bundle_key": bundle_key,`:

```python
        "facts": factset.to_dict(),
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
uv run pytest tests/test_resolve_samples_facts.py -q
```
Expected: `6 passed`

- [ ] **Step 9: Run the existing pipeline suite for regressions**

```bash
uv run pytest tests/test_pipeline_agent_tools.py tests/test_pipeline_params.py -q
```
Expected: all pass. If a test asserted the old vote behaviour, update it to the new expectation and note the change in the commit message — do not weaken the new behaviour to satisfy it.

- [ ] **Step 10: Check whether `Counter` is still used**

```bash
grep -n "Counter" src/chat_nextseek/pipeline/agent_tools.py
```
If there are no remaining uses, remove `from collections import Counter` from the imports.

- [ ] **Step 11: Commit**

```bash
git add src/chat_nextseek/pipeline/agent_tools.py tests/test_resolve_samples_facts.py
git commit -m "feat(pipeline): resolve species from typed evidence, not an exact-string vote

Mouse cohorts resolved to nothing because MUS declares no Species attribute,
and 545 samples lost to a parenthetical. Species now comes from the sample
type, TaxonomyID, Species and Strain in strength order, and only evidence that
qualifies sets a genome."
```

---

### Task 6: Render ambiguity questions

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/seqera/user_params.py` (append)
- Test: `chat_nextseek/tests/test_render_ambiguity.py`

**Interfaces:**
- Consumes: the question dicts from `FactSet.questions()` (Task 2) — `{"name", "reason", "values", "suggestion"}`.
- Produces: `render_ambiguity(questions: list[dict]) -> str`

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_render_ambiguity.py`:

```python
from chat_nextseek.seqera.user_params import render_ambiguity


def test_empty_questions_render_to_empty_string():
    assert render_ambiguity([]) == ""


def test_conflict_shows_every_value_and_its_samples():
    text = render_ambiguity([{
        "name": "species", "reason": "conflict",
        "values": {"mouse": ["D.SEQ-1", "D.SEQ-2"], "human": ["D.SEQ-3"]},
        "suggestion": None,
    }])
    assert "species" in text
    assert "mouse" in text and "human" in text
    assert "D.SEQ-1" in text and "D.SEQ-3" in text
    assert "2 sample" in text          # the split is quantified
    assert "will not guess" in text


def test_partial_names_what_is_known_and_what_is_missing():
    text = render_ambiguity([{
        "name": "species", "reason": "partial",
        "values": {"macaque": ["D.SEQ-1"]}, "suggestion": "macaque",
    }])
    assert "macaque" in text
    assert "narrows" in text


def test_weak_offers_the_reading_as_a_suggestion():
    text = render_ambiguity([{
        "name": "species", "reason": "weak",
        "values": {"mouse": ["D.SEQ-1"]}, "suggestion": "mouse",
    }])
    assert "mouse" in text
    assert "confirm" in text.lower()


def test_missing_says_nothing_was_recorded():
    text = render_ambiguity([{
        "name": "species", "reason": "missing", "values": {}, "suggestion": None,
    }])
    assert "nothing" in text.lower() or "no " in text.lower()


def test_all_questions_batch_into_one_block():
    text = render_ambiguity([
        {"name": "species", "reason": "missing", "values": {}, "suggestion": None},
        {"name": "strategy", "reason": "conflict",
         "values": {"rna-seq": ["A"], "amplicon": ["B"]}, "suggestion": None},
    ])
    assert text.count("I will not guess") == 1
    assert "species" in text and "strategy" in text


def test_long_uid_lists_are_truncated():
    uids = [f"D.SEQ-{i}" for i in range(20)]
    text = render_ambiguity([{
        "name": "species", "reason": "conflict",
        "values": {"mouse": uids, "human": ["D.SEQ-99"]}, "suggestion": None,
    }])
    assert "D.SEQ-0" in text
    assert "D.SEQ-19" not in text
    assert "20 samples" in text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_render_ambiguity.py -q
```
Expected: `ImportError: cannot import name 'render_ambiguity'`

- [ ] **Step 3: Append the implementation**

Append to `chat_nextseek/src/chat_nextseek/seqera/user_params.py`:

```python
# How many supporting UIDs to name before summarising. Enough to recognise the
# samples, short enough that the question stays readable in a chat panel.
_MAX_SHOWN_UIDS = 4

_REASON_LEAD = {
    "conflict": "the samples disagree",
    "partial":  "the metadata narrows it but does not settle it",
    "weak":     "the only evidence is indirect",
    "missing":  "nothing in the metadata records it",
}


def _describe_values(values: dict[str, list[str]]) -> list[str]:
    lines = []
    for value, uids in (values or {}).items():
        shown = ", ".join(uids[:_MAX_SHOWN_UIDS])
        more = f", +{len(uids) - _MAX_SHOWN_UIDS} more" if len(uids) > _MAX_SHOWN_UIDS else ""
        count = f"{len(uids)} sample{'s' if len(uids) != 1 else ''}"
        lines.append(f"  - **{value}** — {count} ({shown}{more})")
    return lines


def render_ambiguity(questions: list[dict[str, Any]]) -> str:
    """The question to put to the user when inference cannot conclude.

    Same voice and shape as ``render_elicitation`` so a declared-required-param
    question and an inference gap read identically. Everything found in one pass
    is batched into a single block: serial questions would burn the agent's step
    budget and read as an interrogation.
    """
    if not questions:
        return ""
    lines = ["Before this can run I need "
             f"{'one thing' if len(questions) == 1 else 'a few things'} "
             "confirmed, because the metadata does not settle "
             f"{'it' if len(questions) == 1 else 'them'}:", ""]
    for question in questions:
        name = question.get("name", "?")
        reason = question.get("reason", "missing")
        lines.append(f"- **{name}** — {_REASON_LEAD.get(reason, _REASON_LEAD['missing'])}.")
        lines += _describe_values(question.get("values") or {})
        suggestion = question.get("suggestion")
        if reason == "partial" and suggestion:
            lines.append(f"  - I can tell it is {suggestion}, but not which one. "
                         "Which is it?")
        elif reason == "weak" and suggestion:
            lines.append(f"  - The indirect reading is **{suggestion}**. "
                         "Can you confirm, or give the right value?")
        elif reason == "conflict":
            lines.append("  - Tell me which to use for the whole run, or split the "
                         "cohort so each run is uniform.")
    lines += ["", "I will not guess: a wrong value here usually produces a "
                  "plausible-looking wrong answer rather than an error."]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_render_ambiguity.py -q
```
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/chat_nextseek/seqera/user_params.py tests/test_render_ambiguity.py
git commit -m "feat(seqera): render inference-ambiguity questions in the elicitation voice"
```

---

### Task 7: Gate `configure_run` on the facts

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py` (function `tool_configure_run`)
- Test: `chat_nextseek/tests/test_configure_run_ambiguity.py`

**Interfaces:**
- Consumes: `state["facts"]` (Task 5), `FactSet.from_dict` / `.questions()` (Task 2), `render_ambiguity` (Task 6).
- Produces: the existing `{"ok": false, "needs_user_input": [...], "ask_the_user": ...}` envelope, now also raised for inference gaps. Success payload gains `"inferred"`.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_configure_run_ambiguity.py`:

```python
import json

import pytest

from chat_nextseek.pipeline import agent_tools


@pytest.fixture
def built(tmp_path):
    """State as it stands after a successful write_samplesheet."""
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1,fastq_2\nD.SEQ-1,,\n")
    return {"pipeline_key": "rnaseq",
            "artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)}}


def run(state, params=None):
    return json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "rnaseq", "params": params or {}}, "."))


def test_missing_species_asks_instead_of_defaulting(built):
    built["facts"] = {}
    built["bundle_key"] = None
    result = run(built)
    assert result["ok"] is False
    assert "species" in result["needs_user_input"]
    assert "I will not guess" in result["ask_the_user"]


def test_conflicting_species_asks_and_writes_nothing(built, tmp_path):
    built["facts"] = {"species": {"value": "mouse", "strength": "exact",
                                  "source": "sample_type:MUS",
                                  "uids": ["D.SEQ-1"], "partial": False}}
    built["bundle_key"] = None
    result = run(built)
    assert result["ok"] is False
    assert not (tmp_path / "params.yml").exists()
    assert not (tmp_path / "launch.yml").exists()


def test_partial_species_asks(built):
    built["facts"] = {"species": {"value": "macaque", "strength": "exact",
                                  "source": "sample_type:NHP",
                                  "uids": ["D.SEQ-1"], "partial": True}}
    built["bundle_key"] = None
    result = run(built)
    assert result["ok"] is False
    assert "species" in result["needs_user_input"]
    assert "macaque" in result["ask_the_user"]


def test_resolved_species_proceeds_and_reports_its_source(built):
    built["facts"] = {"species": {"value": "mouse", "strength": "exact",
                                  "source": "sample_type:MUS",
                                  "uids": ["D.SEQ-1"], "partial": False}}
    built["bundle_key"] = "GRCm39"
    result = run(built)
    assert result["ok"] is True
    assert result["bundle_key"] == "GRCm39"
    assert result["inferred"]["species"]["source"] == "sample_type:MUS"


def test_an_explicit_genome_answer_suppresses_the_question(built):
    built["facts"] = {"species": {"value": "macaque", "strength": "exact",
                                  "source": "sample_type:NHP",
                                  "uids": ["D.SEQ-1"], "partial": True}}
    built["bundle_key"] = None
    result = run(built, params={"genome": "Mmul_10"})
    assert result["ok"] is True
    assert result["bundle_key"] == "Mmul_10"


def test_declared_user_params_are_still_asked_first(tmp_path):
    """ampliseq's primers must take precedence over an inference question."""
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sampleID,forwardReads,reverseReads\nS1,,\n")
    state = {"pipeline_key": "ampliseq", "facts": {}, "bundle_key": None,
             "artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)}}
    result = json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": "ampliseq", "params": {}}, "."))
    assert result["ok"] is False
    assert "FW_primer" in result["needs_user_input"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_configure_run_ambiguity.py -q
```
Expected: FAIL — `test_missing_species_asks_instead_of_defaulting` returns `ok: True`; `KeyError: 'needs_user_input'`.

- [ ] **Step 3: Add the imports**

In `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py`, extend the existing `from ..seqera.user_params import (...)` block to include `render_ambiguity`:

```python
from ..seqera.user_params import (
    missing_user_params,
    render_ambiguity,
    render_elicitation,
    validate_user_params,
)
```

- [ ] **Step 4: Add the gate**

In `tool_configure_run`, immediately after the line `state["bundle_key"] = bundle_key` and before `merged, errors, reference_status = build_run_params(...)`, insert:

```python
    # Inference gaps are asked, never defaulted. This runs after the genome
    # override so an explicit answer from the user closes the question rather
    # than re-raising it. A pipeline that takes no reference at all (ampliseq
    # declares reference_cli_flags: []) does not need a species to run.
    facts = FactSet.from_dict(state.get("facts"))
    ambiguities = facts.questions()
    entry = NFCORE_PIPELINE_CATALOG.get(pipeline_key) or {}
    needs_reference = bool(entry.get("reference_cli_flags"))
    if needs_reference and not bundle_key and not agent_params.get("genome"):
        if not any(q["name"] == "species" for q in ambiguities):
            ambiguities.append({"name": "species", "reason": "missing",
                                "values": {}, "suggestion": None})
    if bundle_key or agent_params.get("genome"):
        ambiguities = [q for q in ambiguities if q["name"] != "species"]
    if ambiguities:
        return json.dumps({
            "ok": False,
            "needs_user_input": [q["name"] for q in ambiguities],
            "ask_the_user": render_ambiguity(ambiguities),
            "message": ("Relay `ask_the_user` to the user in plain text and STOP. "
                        "Do not call conclude, and do not invent values."),
        })
```

- [ ] **Step 5: Report what was inferred**

In the success `return json.dumps({...})` of `tool_configure_run`, add this entry immediately after `"bundle_key": bundle_key,`:

```python
        "inferred": facts.to_dict(),
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_configure_run_ambiguity.py -q
```
Expected: `6 passed`

- [ ] **Step 7: Run the full suite**

```bash
uv run pytest tests/ --ignore=tests/evaluator -q
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/chat_nextseek/pipeline/agent_tools.py tests/test_configure_run_ambiguity.py
git commit -m "feat(pipeline): ask when inference cannot settle the reference

Unresolved, partial and conflicting species now stop configure_run and route
through the existing elicitation channel instead of falling through to a
silent GRCh38 default at submit."
```

---

### Task 8: Data-type fit check

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/seqera/catalog.py`
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py` (function `tool_configure_run`)
- Test: `chat_nextseek/tests/test_strategy_fit_check.py`

**Interfaces:**
- Consumes: the `strategy` fact (Task 4), `FactSet` (Task 2).
- Produces: catalog key `accepted_strategies: list[str]` on the entries below; an extra question dict with `reason: "mismatch"`.

`accepted_strategies` values are the normalised vocabulary the audit found. Only populate entries whose input strategy is unambiguous; an absent key means "check skipped", which is the correct default for the 24 pipelines with no verified data.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_strategy_fit_check.py`:

```python
import json

import pytest

from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG
from chat_nextseek.pipeline import agent_tools


def test_catalog_declares_accepted_strategies_for_the_verified_pipelines():
    for key in ("rnaseq", "scrnaseq", "ampliseq", "atacseq", "chipseq", "methylseq"):
        assert NFCORE_PIPELINE_CATALOG[key].get("accepted_strategies"), key


def test_accepted_strategies_are_normalised_lowercase():
    from chat_nextseek.inference.normalise import normalise
    for key, entry in NFCORE_PIPELINE_CATALOG.items():
        for value in entry.get("accepted_strategies") or []:
            assert value == normalise(value, fact="strategy"), f"{key}: {value!r}"


@pytest.fixture
def built(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1,fastq_2\nD.SEQ-1,,\n")
    return {"pipeline_key": "rnaseq", "bundle_key": "GRCm39",
            "artifacts": {"samplesheet": str(sheet), "base_dir": str(tmp_path)}}


def _facts(strategy):
    return {"species": {"value": "mouse", "strength": "exact",
                        "source": "sample_type:MUS", "uids": ["D.SEQ-1"],
                        "partial": False},
            "strategy": {"value": strategy, "strength": "exact",
                         "source": "D.SEQ.LibraryStrategy", "uids": ["D.SEQ-1"],
                         "partial": False}}


def run(state):
    return json.loads(agent_tools.tool_configure_run(
        object(), state, {"pipeline_key": state["pipeline_key"], "params": {}}, "."))


def test_amplicon_data_against_rnaseq_stops_and_says_so(built):
    built["facts"] = _facts("amplicon")
    result = run(built)
    assert result["ok"] is False
    assert "strategy" in result["needs_user_input"]
    assert "amplicon" in result["ask_the_user"]
    assert "rnaseq" in result["ask_the_user"]


def test_matching_strategy_proceeds(built):
    built["facts"] = _facts("rna-seq")
    assert run(built)["ok"] is True


def test_check_is_skipped_when_the_pipeline_declares_nothing(built):
    built["pipeline_key"] = "fastqrepair"
    built["facts"] = _facts("amplicon")
    assert NFCORE_PIPELINE_CATALOG["fastqrepair"].get("accepted_strategies") is None
    assert run(built)["ok"] is True


def test_check_is_skipped_when_there_is_no_strategy_fact(built):
    built["facts"] = {"species": _facts("rna-seq")["species"]}
    assert run(built)["ok"] is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_strategy_fit_check.py -q
```
Expected: FAIL — `KeyError`/`AssertionError` on `accepted_strategies`.

- [ ] **Step 3: Add `accepted_strategies` to the catalog**

In `chat_nextseek/src/chat_nextseek/seqera/catalog.py`, add an `accepted_strategies` key to each of these entries, alongside its existing `accepted_leaf_sample_types`:

```python
# rnaseq
    "accepted_strategies": ["rna-seq", "total rna", "mrna-seq"],
# scrnaseq
    "accepted_strategies": ["scrna-seq", "rna-seq"],
# ampliseq
    "accepted_strategies": ["amplicon", "targeted capture"],
# atacseq
    "accepted_strategies": ["atac-seq"],
# chipseq
    "accepted_strategies": ["chip-seq"],
# methylseq
    "accepted_strategies": ["bisulfite-seq", "wgbs"],
# sarek
    "accepted_strategies": ["wgs", "wxs", "targeted capture"],
# smrnaseq
    "accepted_strategies": ["mirna-seq", "ncrna-seq"],
# hic
    "accepted_strategies": ["hi-c"],
```

Add this comment immediately above the first one:

```python
    # Normalised LibraryStrategy values this pipeline's input can legitimately be
    # (see inference/normalise.py). Absent = the fit check is skipped, which is
    # the right default for pipelines with no verified NExtSEEK data.
```

- [ ] **Step 4: Add the check**

In `tool_configure_run`, immediately after the `ambiguities = facts.questions()` / `entry = ...` lines added in Task 7 and before the `needs_reference` block, insert:

```python
    # Data-type fit. Today this judgement lives only in the prompt and is pure
    # model reasoning; LibraryStrategy is populated on 100% of D.SEQ, so it can
    # be checked. A mismatch is a question, never a silent param.
    accepted = entry.get("accepted_strategies")
    strategy_fact = facts.best("strategy")
    if accepted and strategy_fact and strategy_fact.applies():
        if strategy_fact.value not in accepted:
            ambiguities.append({
                "name": "strategy", "reason": "mismatch",
                "values": {strategy_fact.value: list(strategy_fact.uids)},
                "suggestion": None,
                "detail": (f"these samples are {strategy_fact.value} data, but "
                           f"{pipeline_key} expects {' or '.join(accepted)}"),
            })
```

- [ ] **Step 5: Render the mismatch reason**

In `chat_nextseek/src/chat_nextseek/seqera/user_params.py`, add to `_REASON_LEAD`:

```python
    "mismatch": "the data type does not match this pipeline",
```

and in `render_ambiguity`, inside the per-question loop, add this branch before the closing of the `if/elif` chain:

```python
        elif reason == "mismatch":
            lines.append(f"  - {question.get('detail', '')}")
            lines.append("  - Confirm you want to run it anyway, or tell me which "
                         "pipeline to use instead.")
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_strategy_fit_check.py tests/test_render_ambiguity.py -q
```
Expected: `12 passed`

- [ ] **Step 7: Commit**

```bash
git add src/chat_nextseek/seqera/catalog.py src/chat_nextseek/seqera/user_params.py src/chat_nextseek/pipeline/agent_tools.py tests/test_strategy_fit_check.py
git commit -m "feat(seqera): check data-type fit against LibraryStrategy

LibraryStrategy is populated on 100% of D.SEQ, so 'is this pipeline right for
these samples' no longer has to be pure model judgement."
```

---

### Task 9: Emitter consumes the platform rule

Fixes a live defect: `"\bont\b"` in `emitter.py` is a non-raw string, so its runtime value is `'\x08ont\x08'` and the Oxford Nanopore hint can never match.

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/seqera/emitter.py`
- Test: `chat_nextseek/tests/test_platform_columns_and_batch2.py` (add to the existing file)

**Interfaces:**
- Consumes: `platform_from_text` (Task 4).
- Produces: no signature change — `_platform_from_meta` keeps its shape.

- [ ] **Step 1: Write the failing test**

Append to `chat_nextseek/tests/test_platform_columns_and_batch2.py`:

```python
def test_ont_is_recognised_as_nanopore():
    """The \\bont\\b hint was a non-raw string and never matched."""
    from chat_nextseek.seqera.emitter import _platform_from_meta
    assert _platform_from_meta({"Sequencer": "ONT"}) == "nanopore"
    assert _platform_from_meta({"Platform": "ont"}) == "nanopore"


def test_ont_does_not_match_a_substring():
    from chat_nextseek.seqera.emitter import _platform_from_meta
    assert _platform_from_meta({"Notes": "sequenced at the front desk"}) == ""


def test_existing_platform_families_still_resolve():
    from chat_nextseek.seqera.emitter import _platform_from_meta
    assert _platform_from_meta({"Sequencer": "Illumina NovaSeq 6000"}) == "illumina"
    assert _platform_from_meta({"Sequencer": "PacBio Revio"}) == "pacbio"
    assert _platform_from_meta({"Sequencer": "PromethION"}) == "nanopore"
    assert _platform_from_meta({"Sequencer": "Singular G4"}) == "illumina"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_platform_columns_and_batch2.py -q -k ont
```
Expected: FAIL — `assert '' == 'nanopore'`

- [ ] **Step 3: Delete the duplicated hint table**

In `chat_nextseek/src/chat_nextseek/seqera/emitter.py`, delete the `_PLATFORM_HINTS` tuple in its entirety (the block beginning `_PLATFORM_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (`).

- [ ] **Step 4: Delegate to the rule**

Replace the body of `_platform_from_meta` with:

```python
def _platform_from_meta(meta: Mapping[str, Any]) -> str:
    """Best-effort sequencing platform from a sample's metadata, or ''.

    Scans values rather than trusting a field name, because the platform shows up
    under Sequencer, Platform, Instrument and SequencingType depending on who
    curated the row. The matching itself lives in inference.rules so there is one
    hint table, not two — the copy that used to live here had a non-raw "\\bont\\b"
    whose runtime value was '\\x08ont\\x08' and matched nothing.
    """
    blob = " ".join(str(v) for v in (meta or {}).values() if v)
    return platform_from_text(blob)
```

- [ ] **Step 5: Add the import**

Add to the imports of `chat_nextseek/src/chat_nextseek/seqera/emitter.py`:

```python
from ..inference.rules import platform_from_text
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_platform_columns_and_batch2.py -q
```
Expected: all pass, including the pre-existing genomeassembler and pathogensurveillance cases.

- [ ] **Step 7: Check `re` is still used in emitter.py**

```bash
grep -n "re\." src/chat_nextseek/seqera/emitter.py | head
```
If there are no remaining uses, remove `import re`.

- [ ] **Step 8: Commit**

```bash
git add src/chat_nextseek/seqera/emitter.py tests/test_platform_columns_and_batch2.py
git commit -m "fix(seqera): ONT platform hint never matched

'\\bont\\b' was written as a non-raw string, so its value was '\\x08ont\\x08'
and the dispatcher routed it to a substring test it could never satisfy.
Platform matching now lives in inference.rules, once."
```

---

### Task 10: Remove the last two guessing paths

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/luria/submitter.py`
- Modify: `chat_nextseek/src/chat_nextseek/prompts/pipeline_agent.txt`
- Test: `chat_nextseek/tests/test_luria_submitter.py` (add to the existing file)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_submit_one` raises `ValueError` instead of defaulting the genome.

- [ ] **Step 1: Write the failing test**

Append to `chat_nextseek/tests/test_luria_submitter.py`:

```python
def test_unresolved_genome_refuses_to_submit(tmp_path, monkeypatch):
    """With inference in place, an unresolved species cannot reach the cluster.

    This used to default to GRCh38 with a console-only warning, which is the
    last path by which a non-human cohort could be aligned to the human genome.
    """
    from chat_nextseek.luria import submitter

    launch = tmp_path / "launch.yml"
    launch.write_text(
        "launch:\n  - name: run1\n    pipeline: https://github.com/nf-core/rnaseq\n"
        "    revision: 3.18.0\n")
    (tmp_path / "samplesheet.csv").write_text("sample,fastq_1,fastq_2\nS1,,\n")

    calls = []
    monkeypatch.setattr(submitter, "ssh_run",
                        lambda *a, **k: calls.append(a) or "")
    monkeypatch.setattr(submitter, "scp_file", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(submitter, "prepare_key", lambda key: str(tmp_path / "k"))

    runs = submitter.submit_luria(
        str(launch),
        luria_env={"user": "u", "key": "k", "working_path": "/w",
                   "host": "luria.mit.edu"},
        genome=None,
        launch_params={},
    )
    assert runs == []          # the entry was dropped, not submitted
    assert calls == []         # nothing reached the cluster
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_luria_submitter.py -q -k unresolved_genome
```
Expected: FAIL — the run is submitted and `calls` is non-empty.

- [ ] **Step 3: Replace the silent default**

In `chat_nextseek/src/chat_nextseek/luria/submitter.py`, replace:

```python
    # Genome is the species-resolved iGenomes key (mouse->GRCm39, human->GRCh38) threaded from
    # configure_run; default to GRCh38 loudly rather than silently mis-aligning a non-human cohort.
    run_genome = genome or "GRCh38"
    if not genome:
        print(f"[LURIA][SUBMIT] entry {name!r}: no resolved genome — defaulting to GRCh38 "
              "(VERIFY the cohort is human before trusting results!)")
```

with:

```python
    # Genome is the species-resolved iGenomes key threaded from configure_run.
    # There is no default: this used to fall back to GRCh38 with a console-only
    # warning, which silently aligned non-human cohorts to the human genome.
    # configure_run now asks the user when species cannot be resolved, so an
    # unresolved genome reaching here means that gate was bypassed.
    if not genome:
        raise ValueError(
            f"entry {name!r}: no resolved genome. configure_run must resolve a "
            "species or ask the user before a run can be submitted.")
    run_genome = genome
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_luria_submitter.py -q
```
Expected: all pass. `submit_luria` catches per-entry exceptions and drops the entry, so the `ValueError` surfaces to the agent as `"No runs submitted — check Luria logs."`

- [ ] **Step 5: Narrow the prompt**

In `chat_nextseek/src/chat_nextseek/prompts/pipeline_agent.txt`, in step 6, replace this text:

```
but auto-detection only fires when the metadata has a clean organism value. If detected_species is null, or configure_run returns reference_status "no_bundle" / "unconfigured_no_fallback", infer the organism YOURSELF from the sample metadata (fields like organism, strain, or genotype identify the species even when there's no explicit organism field) and re-call configure_run with genome set to that species (e.g. "mouse", "human", "nhp") or a specific iGenomes key ("GRCm39", "GRCh38"). Don't leave the genome unset for a species you can identify.
```

with:

```
Species is derived deterministically from the lineage (sample type, TaxonomyID, Species, Strain) and reported in `facts` with its source and strength. Do NOT infer the organism yourself: if the evidence is missing, partial or conflicting, configure_run returns `ask_the_user` — relay it verbatim and stop. When the user answers, pass their value as `genome` in configure_run's params.
```

- [ ] **Step 6: Verify no test asserted the removed prompt text**

```bash
grep -rn "infer the organism YOURSELF" tests/ src/ || echo "clean"
```
Expected: `clean`

- [ ] **Step 7: Run the full suite**

```bash
uv run pytest tests/ --ignore=tests/evaluator -q
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/chat_nextseek/luria/submitter.py src/chat_nextseek/prompts/pipeline_agent.txt tests/test_luria_submitter.py
git commit -m "fix(pipeline): stop guessing the genome at both remaining sites

The submitter defaulted to GRCh38 with a console-only warning, and the prompt
told the model to infer the organism itself. Both undercut the inference gate,
so both are removed."
```

---

### Task 11: Fix `pipeline_key` precedence

Adjacent bug the design calls out. `dispatch_pipeline_tool_call` resolves `state.get(...) or tool_input.get(...)`, so state wins and a `resolve_samples` issued after a first `write_samplesheet` silently filters on the previous pipeline's accepted leaf types.

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py` (function `dispatch_pipeline_tool_call`)
- Test: `chat_nextseek/tests/test_pipeline_key_precedence.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: no signature change.

- [ ] **Step 1: Write the failing test**

Create `chat_nextseek/tests/test_pipeline_key_precedence.py`:

```python
from chat_nextseek.pipeline import agent_tools


def test_tool_input_pipeline_key_wins_over_stale_state(monkeypatch):
    """Switching pipeline mid-build must re-filter on the NEW pipeline's types."""
    seen = {}

    def fake_resolve(config, session, state, tool_input, pipeline_key):
        seen["pipeline_key"] = pipeline_key
        return "{}"

    monkeypatch.setattr(agent_tools, "tool_resolve_samples", fake_resolve)
    agent_tools.dispatch_pipeline_tool_call(
        config=object(), session={}, state={"pipeline_key": "rnaseq"},
        name="resolve_samples",
        tool_input={"kind": "explicit_uids", "uids": ["A.ALN-1"],
                    "pipeline_key": "bamtofastq"},
        log_dir=".")
    assert seen["pipeline_key"] == "bamtofastq"


def test_state_pipeline_key_is_used_when_the_tool_input_omits_it(monkeypatch):
    seen = {}

    def fake_resolve(config, session, state, tool_input, pipeline_key):
        seen["pipeline_key"] = pipeline_key
        return "{}"

    monkeypatch.setattr(agent_tools, "tool_resolve_samples", fake_resolve)
    agent_tools.dispatch_pipeline_tool_call(
        config=object(), session={}, state={"pipeline_key": "rnaseq"},
        name="resolve_samples",
        tool_input={"kind": "last_search"}, log_dir=".")
    assert seen["pipeline_key"] == "rnaseq"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pipeline_key_precedence.py -q
```
Expected: FAIL — `assert 'rnaseq' == 'bamtofastq'`

- [ ] **Step 3: Reverse the precedence**

In `dispatch_pipeline_tool_call`, replace:

```python
    if name == "resolve_samples":
        pipeline_key = state.get("pipeline_key") or tool_input.get("pipeline_key") or ""
```

with:

```python
    if name == "resolve_samples":
        # Tool input wins: the model naming a pipeline here is an explicit choice,
        # and state's key is a leftover from an earlier write_samplesheet. State
        # losing meant a mid-build pipeline switch kept filtering on the old
        # pipeline's accepted leaf sample types and returned nothing.
        pipeline_key = tool_input.get("pipeline_key") or state.get("pipeline_key") or ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_pipeline_key_precedence.py -q
```
Expected: `2 passed`

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest tests/ --ignore=tests/evaluator -q
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/chat_nextseek/pipeline/agent_tools.py tests/test_pipeline_key_precedence.py
git commit -m "fix(pipeline): let tool_input's pipeline_key beat stale session state

A resolve_samples after a write_samplesheet filtered on the previous
pipeline's accepted leaf types, so switching pipeline mid-build returned
nothing with no explanation."
```

---

### Task 12: Re-run the audit and record the result

Closes the loop: the design's scope was set by measurement, so the outcome should be measured too.

**Files:**
- Modify: `docs/2026-08-07-pipeline-param-inference-design.md`

- [ ] **Step 1: Run the full suite one final time**

```bash
uv run pytest tests/ --ignore=tests/evaluator -q
```
Expected: all pass.

- [ ] **Step 2: Re-run the coverage audit**

From the repo root:
```bash
docker exec -i nextseek uv run manage.py shell < scripts/audit_metadata_coverage.py
```
Record the "proposed tiers" and "bundle resolution" blocks.

- [ ] **Step 3: Verify the headline number**

The audit's "bundle resolution, case-fold only" block is computed with a case-fold-only matcher and so still shows `species known, NO bundle: 545`. Confirm the normaliser closes that gap:

```bash
cd chat_nextseek && uv run python -c "
from chat_nextseek.inference.normalise import normalise
from chat_nextseek.seqera.pipeline_params import resolve_bundle_for_species
for raw in ['Macaca mulatta (Rhesus)', 'Macaca mulatta', 'Homo Sapiens', 'mouse']:
    print(f'{raw!r:32} -> {normalise(raw)!r:20} -> {resolve_bundle_for_species(normalise(raw))}')
"
```
Expected:
```
'Macaca mulatta (Rhesus)'        -> 'macaca mulatta'     -> Mmul_10
'Macaca mulatta'                 -> 'macaca mulatta'     -> Mmul_10
'Homo Sapiens'                   -> 'homo sapiens'       -> GRCh38
'mouse'                          -> 'mouse'              -> GRCm39
```

If `resolve_bundle_for_species` does not receive normalised input from `tool_resolve_samples`, that is a wiring bug in Task 5 — fix it before proceeding.

- [ ] **Step 4: Append the outcome to the design doc**

Add a short "Outcome" section at the end of `docs/2026-08-07-pipeline-param-inference-design.md` recording the post-implementation tier counts and any rule that fired on nothing.

- [ ] **Step 5: Commit**

```bash
git add docs/2026-08-07-pipeline-param-inference-design.md
git commit -m "docs(specs): record the post-implementation coverage measurement"
```

---

## Self-review

**Spec coverage:** normaliser → Task 1. `Fact`/`FactSet` → Task 2. Species rules incl. the `NHP` partial and both `ExtractedMolecule` spellings → Tasks 3–4. `resolve_samples` wiring and `state["facts"]` → Task 5. `render_ambiguity` → Task 6. `configure_run` gating and batched questions → Task 7. Fit check reviving `accepted_assay_patterns`' purpose → Task 8. Emitter platform consumer and the ONT fix → Task 9. Submitter hard failure and prompt narrowing → Task 10. `pipeline_key` precedence → Task 11. Acceptance re-measurement → Task 12.

**Two spec items deliberately not implemented, and why:**
- *"`reference_bundles.json` lookups route through `normalise`; the table can shrink."* The normalisation happens on the caller's side (facts carry normalised values, Task 5 passes them to `resolve_bundle_for_species`), so `pipeline_params.py` is untouched and the table keeps its redundant entries. Shrinking it is a follow-up with no behavioural effect, and touching a shared lookup used by the dormant Tower path is risk this plan does not need.
- *Removing the dead `accepted_assay_patterns` field.* Task 8 adds `accepted_strategies` beside it rather than replacing it across 31 entries and rewriting `test_catalog_enrichment.py`. Deleting the dead field is a clean follow-up commit.

**Type consistency:** `Fact(name, value, source, strength, uids, partial)` and `.applies()` are used identically in Tasks 2–5, 7, 8. `FactSet.to_dict()`'s five keys match the assertion in Task 2, the state assertion in Task 5 and the fixtures in Tasks 7–8. Question dicts carry `{name, reason, values, suggestion}` everywhere, plus `detail` on the `mismatch` reason introduced in Task 8 and rendered there. `platform_from_text` is defined in Task 4 and consumed in Task 9 under the same name.

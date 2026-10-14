"""The house ontology files in the chat_nextseek context folder: shape, determinism, membership.

Stdlib only: the files are read as JSON, no Django. The reference matcher below is the
`matching` rule of ONTOLOGIES.json, the same three steps the batch-upload skill gives the agent.
"""
from __future__ import annotations

import functools
import itertools
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONTEXT = REPO / "NessieAI/chat_nextseek/src/chat_nextseek/context"
TEMPLATES = REPO / "NessieAI/chat_nextseek/src/chat_nextseek/reports/templates"
INDEX = json.loads((CONTEXT / "ONTOLOGIES.json").read_text(encoding="utf-8"))
ENTRIES = {e["id"]: e for e in INDEX["ontologies"]}
RECORD_KEYS = {"TERM", "DEF", "ONT_SOURCE", "ONT_ID", "SYNONYMS"}
OBO = {"NCBITaxon", "PATO", "UBERON", "MONDO", "UO", "EDAM"}
INDEX_KEYS = {"id", "title", "file", "attributes", "source", "version", "licence", "case_sensitive"}


def records(oid):
    return json.loads((CONTEXT / ENTRIES[oid]["file"]).read_text(encoding="utf-8"))


def norm(value, case_sensitive):
    text = " ".join(str(value).split())
    return text if case_sensitive else text.casefold()


def resolve(oid, value):
    """The `matching` rule: equal to a TERM or a SYNONYM after trim and space collapse."""
    cs = ENTRIES[oid]["case_sensitive"]
    key = norm(value, cs)
    for rec in records(oid):
        if key in {norm(t, cs) for t in [rec["TERM"], *rec["SYNONYMS"]]}:
            return rec
    return None


def test_index_entries_are_complete():
    assert len({e["id"] for e in INDEX["ontologies"]}) == len(INDEX["ontologies"])
    for e in INDEX["ontologies"]:
        assert INDEX_KEYS <= set(e), e["id"]
        assert (CONTEXT / e["file"]).is_file(), e["file"]
        assert isinstance(e["case_sensitive"], bool)


def test_every_ontology_file_is_indexed():
    assert {p.name for p in CONTEXT.glob("ontology_*.json")} == {e["file"] for e in INDEX["ontologies"]}


@pytest.mark.parametrize("oid", sorted(ENTRIES))
def test_record_shape(oid):
    for rec in records(oid):
        assert set(rec) == RECORD_KEYS, rec
        assert isinstance(rec["TERM"], str) and rec["TERM"].strip(), rec
        assert isinstance(rec["DEF"], str) and rec["DEF"].strip(), rec
        assert isinstance(rec["SYNONYMS"], list) and all(isinstance(s, str) for s in rec["SYNONYMS"]), rec
        if rec["ONT_SOURCE"] in OBO:
            assert isinstance(rec["ONT_ID"], str) and rec["ONT_ID"].startswith(rec["ONT_SOURCE"] + ":"), rec
        else:
            assert rec["ONT_ID"] is None, rec


@pytest.mark.parametrize("oid", sorted(ENTRIES))
def test_one_record_per_normalized_key(oid):
    cs = ENTRIES[oid]["case_sensitive"]
    seen = {}
    for rec in records(oid):
        for text in [rec["TERM"], *rec["SYNONYMS"]]:
            key = norm(text, cs)
            assert seen.setdefault(key, rec["TERM"]) == rec["TERM"], f"{oid}: {text!r} on {seen[key]!r} and {rec['TERM']!r}"


def _sra():
    return json.loads((TEMPLATES / "SRA.json").read_text(encoding="utf-8"))["controlled_vocabulary"]


def _geo():
    geo = json.loads((TEMPLATES / "GEO-updated.json").read_text(encoding="utf-8"))
    return [geo["controlled_vocabulary"], geo["schema"]["controlled_vocabulary"]]


def _lists(cv, key):
    if key == "instrument_model":
        out = set(cv.get("instrument_model_flat", []))
        for models in cv["instrument_model_by_platform"].values():
            out.update(models)
        return out
    return set(cv[key])


@pytest.mark.parametrize("oid", ["library_strategy", "library_source", "library_selection", "library_layout", "instrument_model"])
def test_repository_terms_are_in_the_repository_lists(oid):
    sra = _lists(_sra(), oid)
    geo = set().union(*(_lists(cv, oid) for cv in _geo()))
    for rec in records(oid):
        if rec["ONT_SOURCE"] == "SRA":
            assert rec["TERM"] in sra, rec["TERM"]
        if rec["ONT_SOURCE"] == "GEO":
            assert rec["TERM"] in geo, rec["TERM"]


def test_file_format_repository_terms_are_in_the_filetype_lists():
    sra = {t.casefold() for t in _sra()["filetype"]}
    geo = {t.casefold() for cv in _geo() for t in cv["filetype"]}
    for rec in records("file_format"):
        if rec["ONT_SOURCE"] == "SRA":
            assert rec["TERM"].casefold() in sra, rec["TERM"]
        if rec["ONT_SOURCE"] == "GEO":
            assert rec["TERM"].casefold() in geo, rec["TERM"]


def test_generated_vocabularies_are_current():
    sys.path.insert(0, str(REPO))
    from NessieAI.build_tools import gen_house_vocabularies  # noqa: PLC0415

    assert gen_house_vocabularies.check() is None
    assert set(gen_house_vocabularies.build()["field_map"]) == {
        "LibraryStrategy", "LibrarySource", "LibrarySelection", "LibraryDesign", "Sequencer", "DataType",
    }


@pytest.mark.parametrize("oid,value,term,ont_id", [
    ("file_format", "tif", "TIF", None), ("file_format", ".lif", "LIF", None),
    ("file_format", "czi", "CZI", None), ("file_format", ".czi", "CZI", None),
    ("file_format", "oib", "OIB", None), ("file_format", "TIF", "TIF", None),
    ("species", "homo sapiens", "Homo sapiens", "NCBITaxon:9606"),
    ("species", "Homo Sapiens", "Homo sapiens", "NCBITaxon:9606"),
    ("mouse_genotype", "RG", "RaDR+/+; GPT+/+", None),
    ("mouse_genotype", "Rosa26 Direct Repeat (RaDR)+/+; guanine phosphoribosyltransferase (GPT)+/+",
     "RaDR+/+; GPT+/+", None),
    ("library_layout", "Paired", "Paired End", None), ("library_layout", "PAIRED", "Paired End", None),
    ("library_layout", "Paired-end", "Paired End", None), ("library_layout", "paired", "Paired End", None),
    ("sex", "F", "female", None),
    ("mouse_strain", "B6", "C57BL/6", None),
])
def test_siblings_resolve(oid, value, term, ont_id):
    rec = resolve(oid, value)
    assert rec is not None and rec["TERM"] == term
    if ont_id:
        assert rec["ONT_ID"] == ont_id


def _patterns_meet(p, q):
    """True when some `<type>.<attribute>` matches both glob patterns (`*` is their only wildcard)."""
    @functools.lru_cache(maxsize=None)
    def go(i, j):
        if i == len(p):
            return set(q[j:]) <= {"*"}
        if j == len(q):
            return set(p[i:]) <= {"*"}
        if p[i] == "*":
            return go(i + 1, j) or go(i, j + 1)
        if q[j] == "*":
            return go(i, j + 1) or go(i + 1, j)
        return p[i] == q[j] and go(i + 1, j + 1)
    return go(0, 0)


def _keys(oid, case_sensitive):
    return {norm(t, case_sensitive) for rec in records(oid) for t in [rec["TERM"], *rec["SYNONYMS"]]}


def test_ontologies_that_can_cover_one_attribute_share_no_key():
    """The skill takes the first covering ontology that matches, so a value matching two of them
    would silently shadow the later one."""
    shared = set()
    for a, b in itertools.combinations(sorted(ENTRIES), 2):
        if any(_patterns_meet(p, q) for p in ENTRIES[a]["attributes"] for q in ENTRIES[b]["attributes"]):
            shared.add((a, b))
            cs = ENTRIES[a]["case_sensitive"] and ENTRIES[b]["case_sensitive"]
            both = _keys(a, cs) & _keys(b, cs)
            assert not both, f"{a} and {b} both match {sorted(both)}"
    assert ("mouse_genotype", "mouse_strain") in shared  # the pair today; proves the overlap check sees it
    assert _patterns_meet("*.*Units", "OOC.Treatment1DoseUnits") and not _patterns_meet("*.Species", "*.Sex")


def test_units_are_case_sensitive():
    assert resolve("units", "mm") is None
    rec = resolve("units", "mM")
    assert rec is not None and "millimolar" in (rec["TERM"] + rec["DEF"]).casefold()


def test_values_no_term_fits_resolve_to_nothing():
    assert resolve("library_layout", "3DGE") is None
    assert resolve("mouse_genotype", "A") is None  # a bare letter: Aag -/- alone, or the A/J strain
    # A patch release is its own accession: mapping it to the base build loses the patch (asked instead).
    for value in ("GRCh38.p13", "GRCh38.p14", "GRCh37.p13", "GRCm38.p6"):
        assert resolve("genome_assembly", value) is None, value


def test_lab_short_codes_resolve():
    """Operator ruling 2026-10-14: the lab's RaDR/GPT short codes (R = RaDR+/+, G = GPT+/+, A = Aag -/-,
    ATG = AagTg -/+, M = Mgmt-/-) and the strain short names whose full name is in the data map without asking."""
    genotype = {
        "RGA": "RaDR+/+; GPT+/+; Aag -/-",
        "RGATG": "RaDR+/+; GPT+/+; AagTg -/+",
        "RGM": "RaDR+/+; GPT+/+; Mgmt-/-",
        "RGAM": "RaDR+/+; GPT+/+; Aag -/-; Mgmt-/-",
        "R": "RaDR+/+",
        "RA": "RaDR+/+; Aag -/-",
        "GA": "GPT+/+; Aag -/-",
        "RaDR R/R; gpt g/g": "RaDR+/+; GPT+/+",
    }
    for value, term in genotype.items():
        assert resolve("mouse_genotype", value)["TERM"] == term, value
    strain = {"PWK": "PWK/PhJ", "CAST": "CAST/EiJ", "WSB": "WSB/EiJ", "PWD": "PWD/PhJ",
              "CC024J": "CC024/GeniUncJ", "CC024": "CC024/GeniUncJ", "CC011J": "CC011/UncJ", "CC039": "CC039/Unc"}
    for value, term in strain.items():
        assert resolve("mouse_strain", value)["TERM"] == term, value


def test_every_ontology_has_a_public_link():
    """A reply names the ontology it matched against and links it: source_url plus the entry's file."""
    base = INDEX["source_url"]
    assert base.startswith("https://github.com/BioMicroCenter/NExtSEEK/blob/") and base.endswith(
        "/NessieAI/chat_nextseek/src/chat_nextseek/context/")
    for entry in ENTRIES.values():
        assert (CONTEXT / entry["file"]).is_file(), entry["file"]


def test_exact_term_is_already_harmonized():
    for rec in records("file_format"):
        assert resolve("file_format", rec["TERM"])["TERM"] == rec["TERM"]


PART = r"[A-Za-z0-9_]+"
TYPE_RE = re.compile(rf"^(\*|{PART}(\.{PART})*)$")
ATTR_RE = re.compile(rf"^\*?{PART}$|^\*$")


@pytest.mark.parametrize("oid", sorted(ENTRIES))
def test_attribute_patterns_are_well_formed(oid):
    for pattern in ENTRIES[oid]["attributes"]:
        stype, _, attr = pattern.rpartition(".")
        assert stype and attr, pattern
        assert TYPE_RE.match(stype), pattern
        assert ATTR_RE.match(attr), pattern

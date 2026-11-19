"""A named subject (a patient, an animal) is resolved to its one record before the graph agent writes a query.

Production 2026-11-17: "show me the sample tree for <patient name>" was answered by a free-text match over every
record. 343 records mentioned the name; the reply walked all of their trees (708 samples), 41 of the matches sat
under another patient, and about half of the patient's own descendants were missing because they never repeat its
name. The name was never looked up as the record it names. A UID gets that lookup (``uid_check.py``); a name did not.

This is that lookup for a name. A subject is an individual: a patient or an animal, which the catalog marks as a
Source-clade type whose records carry a Sex field (``subject_types``); reagents, cells, microbes and environmental
sources carry none. When a question asks for the tree, lineage or descendants of an identifier-shaped term that is
not a UID, or for the samples, data or records of one written after a word that names an individual ("patient
XY-7", "monkey 77-001", ``subject_words``), the term is matched in the identity fields (Name, ID and the UID) of the
individual type the question names, or of every individual type when it names none, with separators and case
optional (``graph_retry._id_pattern``). "Data for IL-6", "samples from COVID-19 patients" and "samples of CD4 T
cells" put no individual's word before the term, so they run no lookup. Of several matches, those derived from
another match drop out. One left: the plan carries its UID and no longer the term, so the graph agent anchors on it.
Several: the reply lists them and asks. None: nothing is said and the turn runs as it would have (the name may be a
cell line, a reagent or a free-text term). A failed lookup claims nothing. Every read goes through
``tool_neo4j_query``, so a member's lookup runs under the member's scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..graph_retry import _id_pattern, _id_runs
from ..uid_links import sample_url
from .uid_check import UID_RE

#: The tree of one subject, asked "of", "for", "from" or "under" it, then an article and at most one word, then the
#: name: "sample tree for XY-7", "lineage of monkey 77-001". "Stained for CD8" and "children with HIV-1" do not put
#: the name there.
_BEFORE_TREE = re.compile(r"(?:trees?|lineage|derived|descendants?|child(?:ren)?)\s+(?:of|for|from|under)\s+"
                          r"(?:(?:the|a|an)\s+)?(?:[\w.'-]+\s+)?$", re.IGNORECASE)
#: The samples, data or records of one subject, which need the word right before the name to name an individual
#: (``subject_words``): "data for patient XY-7", "samples that came from monkey 77-001". The word is captured.
_BEFORE_ITEMS = re.compile(r"(?:samples?|data|records?|everything)(?:\s+(?:that|which))?"
                           r"(?:\s+(?:came|come|comes|were|was|are|is|collected|taken|drawn|obtained))?"
                           r"\s+(?:of|for|from|under)\s+(?:(?:the|a|an)\s+)?([A-Za-z]+)\s+$", re.IGNORECASE)
#: After the name, on that samples/data path: the name ends its phrase (the question ends, punctuation, "'s", or a
#: word that cannot be the noun the name modifies). "Samples from human CD4 T cells" and "data for mouse IL-6
#: levels" put a marker before a noun, so "human" and "mouse" are adjectives there, not an individual's word.
_NAME_ENDS = re.compile(r"\s*(?:$|[?.,;:!)]|['’]s\b)|\s+(?:and|or|from|in|with|at|of|for|to|on|under|that|which|who"
                        r"|whose|since|before|after|by|please|only)\b", re.IGNORECASE)
#: Or the name, then its tree: "XY-7's sample tree", "XY-7 lineage".
_AFTER_NAME = re.compile(r"^(?:'s)?\s+(?:sample\s*)?(?:trees?|lineage|descendants)\b", re.IGNORECASE)
#: A turn that is only the tree page's name ("Sampletree"): the parser carried the subject from the previous question
#: (``agents/parser.py`` ``_page_word_turn``).
_TREE_ONLY = re.compile(r"\s*sample\s*-?\s*trees?\s*[?.!]*\s*", re.IGNORECASE)

#: The identity fields: the record's Name and ID attributes and its UID, lower-cased, each whole-string matched
#: against the separator-tolerant pattern. ``s.type IN $types`` seeks the Sample.type range index.
LOOKUP_CYPHER = (
    "MATCH (s:Sample) WHERE s.type IN $types\n"
    "  AND (toLower(toString(s.Name)) =~ $pattern OR toLower(toString(s.ID)) =~ $pattern"
    " OR toLower(s.uuid) =~ $pattern)\n"
    "RETURN s.uuid AS uuid, s.type AS type, s.Name AS name, s.ID AS id\n"
    "ORDER BY uuid LIMIT $cap"
)
#: Which of the matches are derived from another match (a visit under its patient): those drop out.
DERIVED_CYPHER = (
    "UNWIND $uids AS u\n"
    "MATCH (s:Sample {uuid: u})-[:DERIVED_FROM*1..12]->(a:Sample)\n"
    "WHERE a.uuid IN $uids\n"
    "RETURN DISTINCT s.uuid AS uuid"
)
#: Matches read at most; more than this many roots is a list to choose from either way.
LOOKUP_CAP = 50
#: Candidates listed in a reply that asks which one.
LIST_MAX = 10


@dataclass(frozen=True)
class SubjectCheck:
    term: str                      # the name as the plan carried it
    types: tuple[str, ...]         # the types searched
    roots: tuple[dict, ...] = ()   # the matches no other match is derived from: {uuid, type, name, id}
    truncated: bool = False        # the lookup hit LOOKUP_CAP


def asks_about_one_subject(question: str | None, term: str, words: frozenset[str] = frozenset()) -> bool:
    """The question asks for the tree or lineage of ``term`` (written with any separators), or for the samples, data
    or records of it written after one of ``words`` (``subject_words``), or is only the tree page's name."""
    q = question or ""
    if _TREE_ONLY.fullmatch(q):
        return True
    runs = _id_runs(term)
    if not runs:
        return False
    for m in re.finditer(r"(?<![a-z0-9])" + r"[-._ /]?".join(map(re.escape, runs)) + r"(?![a-z0-9])", q, re.I):
        before = q[:m.start()]
        if _BEFORE_TREE.search(before) or _AFTER_NAME.match(q[m.end():]):
            return True
        items = _BEFORE_ITEMS.search(before)
        if items and _singular(items.group(1).lower()) in words and _NAME_ENDS.match(q[m.end():]):
            return True
    return False


def _singular(word: str) -> str:
    return word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word


def subject_terms(plan: Any) -> list[str]:
    """The identifier-shaped keywords of the plan that are not UIDs (``graph_retry._id_runs``), in order."""
    filters = getattr(plan, "filters", None)
    resolved = getattr(plan, "resolved", None)
    terms = [t.strip() for t in list(getattr(filters, "keywords", None) or []) + list(getattr(resolved, "keywords",
                                                                                            None) or [])
             if isinstance(t, str) and not UID_RE.fullmatch(t.strip()) and _id_runs(t) is not None]
    return list(dict.fromkeys(terms))


def subject_term(plan: Any, question: str | None = None, words: frozenset[str] = frozenset()) -> str | None:
    """The first of ``subject_terms`` the question asks about as a subject ("CD4 samples for patient XY-7" is about
    XY-7, not CD4), or None.

    ponytail: one subject per turn; a question naming two subjects resolves the first and leaves the second to the
    graph agent as before."""
    return next((t for t in subject_terms(plan) if asks_about_one_subject(question, t, words)), None)


def _catalog_rows(config: Any) -> list[dict]:
    rows = getattr(config, "FULL_SAMPLETYPES", None)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


#: The field that marks an individual among the Source-clade types: a patient or an animal has a sex; an antibody, a
#: chemical, a cell line, a microbe or a water sample has none.
INDIVIDUAL_FIELD = "Sex"
_METADATA_KEYS = ("Required Metadata", "Standard Metadata", "Possible Metadata Fields")
#: The word parts of an attribute title (``BaselineCohort`` holds ``cohort``), as ``field_words`` splits them.
_TITLE_PART = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def _fields(row: dict) -> set[str]:
    return {f.strip() for k in _METADATA_KEYS for f in str(row.get(k) or "").split(",") if f.strip()}


def _individuals(config: Any) -> list[dict]:
    """The catalog rows of the individual types: Source clade, with ``INDIVIDUAL_FIELD`` among their fields."""
    return [r for r in _catalog_rows(config)
            if r.get("SampleType") and str(r.get("Clade") or "").strip().lower() == "source"
            and INDIVIDUAL_FIELD in _fields(r)]


def _words(text: Any) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", str(text or ""))}


def subject_words(config: Any) -> frozenset[str]:
    """The words that name an individual before its name ("patient XY-7", "monkey 77-001", "animal 77-001"): every
    word of an individual type's catalog name, and every word of its tags that no other type's name or tags uses and
    no attribute title holds as a word part. So "study" in "study participant" and "model" in "animal model" are not
    one, nor "cohort" or "trial": a field's value ("cohort C-2") is not an individual's name."""
    rows = _catalog_rows(config)
    individuals = _individuals(config)
    codes = {r["SampleType"] for r in individuals}
    others: set[str] = set()
    for r in rows:
        others |= {_singular(p.lower()) for f in _fields(r) for p in _TITLE_PART.findall(f)}
        if r.get("SampleType") not in codes:
            others |= _words(r.get("Name")) | _words(r.get("Tags"))
    out: set[str] = set()
    for r in individuals:
        out |= _words(r.get("Name")) | {w for w in _words(r.get("Tags")) if w not in others
                                        and _singular(w) not in others}
    return frozenset(out)


def subject_types(config: Any, plan: Any) -> list[str]:
    """The individual types the question names (the parser's sampletype_code and the resolved sample types), else
    every individual type of the catalog. A named type that is not one ("D.SEQ data for patient X") is what is
    asked for, not what the name names. No individual type in the catalog: none, so no lookup runs (never every
    Source type, which would anchor "data for IL-6" on an antibody)."""
    named = [str(getattr(getattr(plan, "filters", None), "sampletype_code", None) or "")]
    named += [str(t.get("code") if isinstance(t, dict) else getattr(t, "code", "") or "")
              for t in getattr(getattr(plan, "resolved", None), "sampletypes", None) or []]
    named = list(dict.fromkeys(c for c in named if c))
    individuals = [str(r["SampleType"]) for r in _individuals(config)]
    return [c for c in named if c in individuals] or individuals


def check_subject(config: Any, question: str | None, plan: Any, *,
                  run: Callable[..., dict]) -> SubjectCheck | None:
    """The lookup, or None when the turn is not about one named subject or a read failed (which claims nothing).
    ``run`` is ``tool_neo4j_query``, so the caller's graph scope applies."""
    term = subject_term(plan, question, subject_words(config))
    if not term:
        return None
    types = subject_types(config, plan)
    if not types:
        return None
    pattern = _id_pattern(_id_runs(term))
    found = run(config, LOOKUP_CYPHER, {"types": types, "pattern": pattern, "cap": LOOKUP_CAP})
    if not isinstance(found, dict) or not found.get("ok"):
        return None
    rows = [r for r in found.get("data") or [] if isinstance(r, dict) and r.get("uuid")]
    rows = list({str(r["uuid"]): r for r in rows}.values())
    roots = rows
    if len(rows) > 1:
        derived = run(config, DERIVED_CYPHER, {"uids": [str(r["uuid"]) for r in rows]})
        if not isinstance(derived, dict) or not derived.get("ok"):
            return None
        below = {str(r.get("uuid")) for r in derived.get("data") or [] if isinstance(r, dict)}
        roots = [r for r in rows if str(r["uuid"]) not in below]
    return SubjectCheck(term=term, types=tuple(types),
                        roots=tuple({k: r.get(k) for k in ("uuid", "type", "name", "id")} for r in roots),
                        truncated=len(rows) >= LOOKUP_CAP)


def _describe(root: dict) -> str:
    bits = [f"{root.get('type')}"]
    if root.get("name") not in (None, ""):
        bits.append(f"Name {root['name']}")
    if root.get("id") not in (None, ""):
        bits.append(f"ID {root['id']}")
    return f"{root['uuid']} ({', '.join(bits)})"


def subject_notes(check: SubjectCheck | None) -> tuple[str | None, list[str]]:
    """``(note for the graph agent, notes for the reply)``; nothing when there was no check or nothing matched (the
    turn then runs as it would have: the name may be a cell line, a reagent or a free-text term)."""
    if check is None or not check.roots:
        return None, []
    term, types = check.term, ", ".join(check.types)
    if len(check.roots) == 1:
        root = check.roots[0]
        uid = root["uuid"]
        agent = (f"'{term}' names one record: {_describe(root)}. Anchor on that UID ({{uuid: '{uid}'}}): its tree, "
                 "samples or data are the samples derived from it ('Everything derived from one UID'), with their "
                 f"types. Do not match '{term}' in search_text: records that only mention it are not this subject's.")
        reply = (f"'{term}' is the record {_describe(root)}; the answer is about that record and the samples "
                 f"derived from it. Give counts by sample type and link its tree page {sample_url(uid)}.")
    else:
        shown = "; ".join(_describe(r) for r in check.roots[:LIST_MAX])
        more = f" and {len(check.roots) - LIST_MAX} more" if len(check.roots) > LIST_MAX else ""
        agent = (f"'{term}' names {len(check.roots)} separate {types} records: {shown}{more}. Return those records "
                 "by UID (s.uuid IN [...]) with their type, Name and ID; do not walk their trees.")
        reply = (f"'{term}' matches {len(check.roots)} separate records by Name, ID or UID: {shown}{more}. List "
                 "them and ask which one the user means before giving any tree.")
    return "SUBJECT CHECK (run before your query):\n- " + agent, [reply]


def plan_with_subject(plan: Any, check: SubjectCheck | None) -> Any:
    """The plan anchored on the resolved records: their UIDs added to ``filters.uids`` and the term taken out of
    ``filters.keywords`` and ``resolved.keywords``, so neither the plan nor the notes the graph agent reads still
    point it at a text match. Unchanged (the same object) when nothing resolved."""
    if check is None or not check.roots:
        return plan
    uids = list(dict.fromkeys(list(plan.filters.uids or []) + [str(r["uuid"]) for r in check.roots[:LIST_MAX]]))
    keep = [k for k in plan.filters.keywords or [] if str(k).strip() != check.term]
    update = {"filters": plan.filters.model_copy(update={"uids": uids, "keywords": keep})}
    resolved = getattr(plan, "resolved", None)
    if resolved is not None and hasattr(resolved, "model_copy"):
        update["resolved"] = resolved.model_copy(update={
            "keywords": [k for k in resolved.keywords or [] if str(k).strip() != check.term]})
    return plan.model_copy(update=update)

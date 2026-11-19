"""A named subject (a patient, an animal) is resolved to its one record before the graph agent writes a query.

Production 2026-11-17: "show me the sample tree for <patient name>" was answered by a free-text match over every
record. 343 records mentioned the name; the reply walked all of their trees (708 samples), 41 of the matches sat
under another patient, and about half of the patient's own descendants were missing because they never repeat its
name. The name was never looked up as the record it names. A UID gets that lookup (``uid_check.py``); a name did not.

This is that lookup for a name. When a question asks for the tree, samples or data of a subject named by an
identifier-shaped term that is not a UID, the term is matched in the identity fields (Name, ID and the UID) of the
type the question names, or of the catalog's Source-clade types when it names none, with separators and case
optional (``graph_retry._id_pattern``). Of several matches, those derived from another match drop out (a visit
under its patient). One left: the plan carries its UID and no longer the term, so the graph agent anchors on it.
Several: the reply lists them and asks. None: the reply says so before any text search. A failed lookup claims
nothing. Every read goes through ``tool_neo4j_query``, so a member's lookup runs under the member's scope.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..graph_retry import _id_pattern, _id_runs
from ..uid_links import sample_url
from .uid_check import UID_RE

#: The question asks for what belongs to one subject: its tree, lineage, derived samples or data. "Samples mentioning
#: X" is a text search and does not match ("of", "for" or "from" must follow the noun).
_SUBJECT_ASK = re.compile(
    r"sample\s*-?\s*trees?|\btrees?\b|\blineage\b|\bderived\b|\bdescend\w*|\bchild(?:ren)?\b"
    r"|\b(?:samples?|data|records?|everything)\s+(?:of|for|from|under)\b",
    re.IGNORECASE)

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


def asks_about_one_subject(question: str | None) -> bool:
    """The question asks for the tree, lineage, samples or data of something it names."""
    return bool(_SUBJECT_ASK.search(question or ""))


def subject_term(plan: Any) -> str | None:
    """The first identifier-shaped keyword of the plan that is not a UID (``graph_retry._id_runs``), or None.

    ponytail: one subject per turn; a question naming two subjects resolves the first and leaves the second to the
    graph agent as before."""
    filters = getattr(plan, "filters", None)
    resolved = getattr(plan, "resolved", None)
    for term in list(getattr(filters, "keywords", None) or []) + list(getattr(resolved, "keywords", None) or []):
        if isinstance(term, str) and not UID_RE.fullmatch(term.strip()) and _id_runs(term) is not None:
            return term.strip()
    return None


def _catalog_rows(config: Any) -> list[dict]:
    rows = getattr(config, "FULL_SAMPLETYPES", None)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def subject_types(config: Any, plan: Any) -> list[str]:
    """The type the question names (the parser's sampletype_code, else the resolved sample types), else every
    Source-clade type of the catalog."""
    code = getattr(getattr(plan, "filters", None), "sampletype_code", None)
    if code:
        return [str(code)]
    named = [str(t.get("code") if isinstance(t, dict) else getattr(t, "code", "") or "")
             for t in getattr(getattr(plan, "resolved", None), "sampletypes", None) or []]
    named = [c for c in named if c]
    if named:
        return named
    return [str(r["SampleType"]) for r in _catalog_rows(config)
            if r.get("SampleType") and str(r.get("Clade") or "").strip().lower() == "source"]


def check_subject(config: Any, question: str | None, plan: Any, *,
                  run: Callable[..., dict]) -> SubjectCheck | None:
    """The lookup, or None when the turn is not about one named subject or a read failed (which claims nothing).
    ``run`` is ``tool_neo4j_query``, so the caller's graph scope applies."""
    if not asks_about_one_subject(question):
        return None
    term = subject_term(plan)
    types = subject_types(config, plan) if term else []
    if not term or not types:
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
    """``(note for the graph agent, notes for the reply)``; nothing when there was no check."""
    if check is None:
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
    elif check.roots:
        shown = "; ".join(_describe(r) for r in check.roots[:LIST_MAX])
        more = f" and {len(check.roots) - LIST_MAX} more" if len(check.roots) > LIST_MAX else ""
        agent = (f"'{term}' names {len(check.roots)} separate {types} records: {shown}{more}. Return those records "
                 "by UID (s.uuid IN [...]) with their type, Name and ID; do not walk their trees.")
        reply = (f"'{term}' matches {len(check.roots)} separate records by Name, ID or UID: {shown}{more}. List "
                 "them and ask which one the user means before giving any tree.")
    else:
        agent = (f"No {types} record has '{term}' as its Name, ID or UID (separators and case ignored). Match "
                 f"'{term}' in search_text: those are records that mention it, not one subject's tree.")
        reply = (f"No {types} record is named or numbered '{term}' (separators and case ignored). Say that first; "
                 "then give the result as records that mention it, labelled as a text search, not as its tree.")
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

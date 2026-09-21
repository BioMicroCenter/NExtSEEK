"""A description of the query that ran, in words a researcher can read.

Decision D1. The chatter used to be told what the user asked for and what came back,
and nothing whatever about the query in between: no endpoint, no method, no
``requestBody``, no ``filter_searchText``, no Cypher, no parser filters, no
``intent_summary``. That was deliberate — the comment in ``agents/chatter.py`` says
the LLM never sees them — and the reason is sound: a biologist must not be answered
with retrieval mechanics.

The cost of it is three production failures out of 84 turns, all the same shape: the
reply described a result as if it answered a question the query never asked.

* **B7** (wesselr 462) "list all the human patient samples (PAT) associated with
  MDL-250912LAU-1" fetched the whole lineage of the model and never filtered to PAT.
  The reply reported 1,904 matching records; none of the nine it showed were PAT.
* **B8** (wesselr 437) searched ``D.FLOW`` and ``D.CYTOF`` and the reply named
  ``D.FCS``. The review's fix is "name types in the reply from the query that ran".
* **B13** (mplaster 501/502) counted every mouse with transcriptomic descendants.
  The plan's own note said the ``CC`` keyword filter could not be applied in the
  graph, and the reply still called the 731 a subset of the CC mice.

None of the three is a hallucination. In each, the gap between the question and the
executed query was computable from arguments ``chatter_agent_answer`` already
receives, and was thrown away before the prompt was built.

So the middle path: the chatter is told **what the executed query constrained**, and
**what the user asked for that it did not constrain**, in user-facing vocabulary. It
is still told nothing about how the query was written. ``render_query_scope`` emits
only entity codes, entity names and one fixed English phrase per kind of search, and
``tests/chat_nextseek/test_query_scope.py`` asserts that no endpoint path, HTTP verb,
Cypher fragment or request-body field name can reach the prompt through it.

**The gap is measured by containment, not inference.** Every constraint the user asked
for is one value; a constraint counts as applied when that value appears, bounded by
non-identifier characters and case-insensitively, in the text of the query that was
actually dispatched. A value that happens to appear for an unrelated reason is read as
applied, so this under-reports the gap and never invents one. When nothing describes
an executed query at all — the reporter path has no query text — the scope reports
itself as unmeasurable and claims no gap, because "the query ignored your filter" is
exactly the kind of confident false statement this is here to prevent.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .lab_code import fold

#: One English phrase per endpoint, for the single sentence the reply may say about
#: how the answer was obtained. Display only: nothing here routes, and the generic
#: fallback is what an endpoint added later gets until someone writes it a phrase.
_SEARCH_KIND_BY_ENDPOINT: dict[str, str] = {
    "/nextseek_api/samples/advanced_search/": "a keyword search over sample records",
    "/nextseek_api/admin/samples/retrieve/":
        "a lookup of the named samples and everything derived from them",
    "/nextseek_api/sample-tree/{uid}/tree/": "the lineage tree of a named sample",
    "/nextseek_api/sample_types/get_parents/parents_by_child_types/":
        "the sample types that can be parents of the requested types",
    "/nextseek_api/sample_types/": "the sample type catalog",
    "/nextseek_api/assays/": "the assay catalog",
    "/nextseek_api/projects/": "the project list",
    "/nextseek_api/investigations/": "the investigation list",
    "/nextseek_api/sops/": "the protocol (SOP) list",
    "/nextseek_api/people/": "the list of registered SEEK users",
}

_GRAPH_SEARCH_KIND = "a graph query over the sample network"
_REPORT_SEARCH_KIND = "an aggregated project report"
_GENERIC_SEARCH_KIND = "a database search"

#: Free text the query's author wrote for a human is useful (B13's caveat lived
#: there) and is the most likely place for mechanics to leak, so it is capped.
_NOTE_MAX_CHARS = 400


@dataclass
class QueryScope:
    """What the executed query constrained, and what it was asked to and did not."""

    searched: str = _GENERIC_SEARCH_KIND
    applied: list[str] = field(default_factory=list)
    not_applied: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: False when nothing in hand describes an executed query, so no claim about
    #: applied or dropped constraints can be made either way.
    measurable: bool = False


def _uniq(values: Any) -> list[str]:
    """Non-empty stringified values, order preserved, duplicates dropped."""
    out: list[str] = []
    for value in values or []:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out


def _codes_and_names(items: Any) -> list[tuple[str, str | None]]:
    """``[{code, name}]`` (or bare strings) as ``(code, name)`` pairs."""
    pairs: list[tuple[str, str | None]] = []
    for item in items or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            name = (item.get("name") or None) and str(item["name"]).strip()
        else:
            code, name = str(item or "").strip(), None
        if code and code not in [existing for existing, _ in pairs]:
            pairs.append((code, name if name and name != code else None))
    return pairs


def _label(kind: str, value: str, name: str | None = None) -> str:
    if kind == "keyword":
        return f'keyword "{value}"'
    if name:
        return f"{kind} {value} ({name})"
    return f"{kind} {value}"


def _asked_for(
    entity_result: dict, parser_plan: dict, user_query: str | None = None,
) -> list[tuple[str, str, str, str | None]]:
    """Every constraint the turn asked for, as ``(kind, value, label, name)``.

    Both the entity agent's resolution and the parser's filters are read: the parser
    can add a filter the entity agent never resolved (a UID, a lab code) and the
    entity agent can resolve one the parser dropped, and a reply that misses either
    is the failure this exists to catch.

    A scientist is asked for as ``scientist <name>`` and its value is the surname (the
    part before a comma in ``Last, First``, else the last token): a graph query may
    match the surname alone, and wherever the full name appears the surname does too, so
    this reads "the full name or its surname appears" and can only under-report. The
    entity agent also appends every scientist to ``keywords``, so a keyword equal to a
    scientist once folded is skipped rather than counted twice. A lab is labelled with
    the name ``lab_matches`` gives its code, whichever side asked for it.

    With the question in hand, an assay, project or keyword the question never
    mentions is not counted as asked for: the entity step over-resolves ("Antibody
    Treatment" for "cd8 depletion", a "Published Data" project read into a -PUB UID),
    and a query that rightly ignored such a guess was reported as having dropped it.
    Sample types, scientists, UIDs and lab codes are always counted: "monkeys" asks for
    NHP without saying it.
    """
    filters = parser_plan.get("filters") or {}
    asked: list[tuple[str, str, str, str | None]] = []
    _guessable = {"assay", "project", "keyword"}

    def _add(kind: str, value: str, name: str | None = None) -> None:
        label = _label(kind, value, name)
        # Deduplicate on (kind, value), not on the label: the entity agent resolves a
        # sample type with its name and the parser's filter carries the bare code, so
        # labelling alone let one type be asked for twice and reported twice.
        if not value or any(kind == k and value == v for k, v, _, _ in asked):
            return
        if any(label == existing for _, _, existing, _ in asked):
            return
        if user_query is not None and kind in _guessable and not _named_in(user_query, value, name):
            return
        asked.append((kind, value, label, name))

    def _surname(name: str) -> str:
        family = name.split(",", 1)[0] if "," in name else name
        tokens = family.split()
        return tokens[-1].strip(".,") if tokens else ""

    scientists: list[str] = []
    scientist_keys: set[str] = set()
    for name in _uniq(entity_result.get("scientists")):
        if fold(name) not in scientist_keys:
            scientist_keys.add(fold(name))
            scientists.append(name)

    lab_names: dict[str, list[str]] = {}
    for match in entity_result.get("lab_matches") or []:
        if not isinstance(match, dict):
            continue
        code = str(match.get("code") or "").strip()
        name = str(match.get("name") or "").strip()
        if code and name and name not in lab_names.setdefault(code, []):
            lab_names[code].append(name)

    for code, name in _codes_and_names(entity_result.get("sampletypes")):
        _add("sample type", code, name)
    if filters.get("sampletype_code"):
        _add("sample type", str(filters["sampletype_code"]))

    for code, name in _codes_and_names(entity_result.get("assays")):
        _add("assay", code, name)
    for code in _uniq(filters.get("assay_codes")):
        _add("assay", code)

    for value in _uniq(entity_result.get("keywords")) + _uniq(filters.get("keywords")):
        if fold(value) in scientist_keys:
            continue
        _add("keyword", value)
    for name in scientists:
        label = f"scientist {name}"
        surname = _surname(name)
        if surname and all(label != existing for _, _, existing, _ in asked):
            asked.append(("scientist", surname, label, name))
    for value in _uniq(filters.get("uids")):
        _add("sample", value)
    for value in _uniq(entity_result.get("projects")):
        _add("project", value)
    for value in _uniq(entity_result.get("lab_codes")) + _uniq(filters.get("lab_codes")):
        _add("lab", value, " or ".join(lab_names.get(value, [])) or None)

    return asked


def _executed_text(api_plan: dict | None, graph_plan: dict | None) -> str | None:
    """The text of the query that was dispatched, or None when there is none.

    This string is never shown to anyone: it is only the haystack the containment
    test runs over.
    """
    parts: list[str] = []
    if graph_plan:
        cypher = str(graph_plan.get("cypher") or "")
        if cypher.strip():
            parts.append(cypher)
        parts.append(json.dumps(graph_plan.get("parameters") or {}, default=str))
    if api_plan:
        for key in ("requestBody", "queryParameters"):
            parts.append(json.dumps(api_plan.get(key) or {}, default=str))
        parts.append(str(api_plan.get("endpoint") or ""))
    if not parts:
        return None
    joined = "".join(parts)
    return joined if joined.strip() else None


def _is_applied(value: str, haystack: str) -> bool:
    """Whether the query constrained on ``value``.

    Bounded on both sides by anything that is not an identifier character, so a
    two-letter tag like ``CC`` is not read as applied because the Cypher happens to
    say ``ACCESSION``, while ``PAT`` inside ``PAT-250912LAU-1`` is — that really is
    the same scope. ``-`` and ``.`` are boundaries on purpose: ``A.GEX`` and a UID
    prefix must both match.
    """
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(value) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, haystack, re.IGNORECASE) is not None


def _type_label(code: str) -> str:
    """The graph's label for a sample type code: ``T_`` plus the code with every character
    outside [A-Za-z0-9_] replaced by ``_`` (``RNA`` is ``T_RNA``, ``D.SEQ`` is ``T_D_SEQ``).

    The label hides the bare code behind an identifier character, so ``_is_applied``
    alone read ``MATCH (s:T_RNA)`` as not constraining RNA: 19 of the 30 Pilot A v2
    replies (2026-09-18) opened by saying the sample type was not applied.
    """
    return "T_" + re.sub(r"[^A-Za-z0-9_]", "_", code)


def _type_is_applied(code: str, haystack: str) -> bool:
    return _is_applied(code, haystack) or _is_applied(_type_label(code), haystack)


def _keyword_is_applied(keyword: str, haystack: str) -> bool:
    """A keyword counts as applied when it, or any of its words of three or more
    characters, is in the query: "RIN score" is constrained by ``s.RIN > 7``. Looser
    than the other kinds on purpose, in the direction this module errs in: it can miss
    a dropped keyword, never invent one."""
    return _is_applied(keyword, haystack) or _fragment_is_applied(keyword, haystack)


def _fragment_is_applied(value: str, haystack: str) -> bool:
    """Whether any word of ``value`` of three or more characters is in the query.

    The looser half of ``_keyword_is_applied``, lifted out so assays can use it too.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", value) if len(w) >= 3]
    return len(words) > 1 and any(_is_applied(w, haystack) for w in words)


def _in_a_type_token(word: str, haystack: str) -> bool:
    """Whether ``word`` appears inside a sample-type label or code in the query.

    Flow Cytometry's data lands on D.FLOW samples, which the graph agent writes as the
    label ``T_D_FLOW``. The word is not a free match: it has to sit inside a ``T_`` label
    or a dotted type code, so "Flow" counts against ``:T_D_FLOW`` and not against a
    property called ``Workflow``.
    """
    for token in re.findall(r"T_[A-Za-z0-9_]+|\b[A-Z]\.[A-Z0-9]+\b", haystack):
        if word.lower() in re.sub(r"[^a-z0-9]", "", token.lower()):
            return True
    return False


def _assay_is_applied(code: str, name: str | None, haystack: str) -> bool:
    """Whether the query constrained on an assay.

    An assay is asked for by its full title, and the query almost never carries that
    title verbatim. The graph agent is told to write a lowercased fragment of it, or to
    filter the edge property ``internal_assay_title``, or to reach the assay's data type
    by its label. An exact containment test sees none of those, so six correct answers in
    the 2026-09-18 runs opened by saying the assay had not been applied -- one of them
    over a Cypher that filtered ``internal_assay_title`` on exactly the right term.

    Three ways to count, in the direction this module errs (it can miss a dropped assay,
    it does not invent one): the title or code itself, any substantial word of it, or a
    word of it inside a sample-type label.
    """
    for candidate in (code, name):
        if not candidate:
            continue
        if _is_applied(candidate, haystack) or _fragment_is_applied(candidate, haystack):
            return True
        for word in re.split(r"[^A-Za-z0-9]+", candidate):
            if len(word) >= 3 and word.lower() not in _GENERIC_LAST_WORDS and _in_a_type_token(word, haystack):
                return True
    return False


def _folded(text: str) -> str:
    return " " + " ".join(re.split(r"[^a-z0-9]+", str(text or "").lower())).strip() + " "


#: A trailing word an entity name carries that a question drops ("CometChip Assay",
#: asked as "CometChip").
_GENERIC_LAST_WORDS = frozenset({"assay", "assays", "analysis", "data", "sample", "samples", "file", "files"})


def _named_in(question: str, value: str, name: str | None = None) -> bool:
    """Whether the question itself mentions this entity, by code or by name.

    Case, punctuation and spacing are folded (``western blot``, ``Western-Blot``,
    ``CometChip`` and ``Comet Chip`` all match), and a generic last word of the name
    may be missing.
    """
    q = _folded(question)
    q_compact = q.replace(" ", "")
    for candidate in (value, name):
        if not candidate:
            continue
        folded = _folded(candidate)
        words = folded.split()
        forms = [folded]
        if len(words) > 1 and words[-1] in _GENERIC_LAST_WORDS:
            forms.append(" " + " ".join(words[:-1]) + " ")
        for form in forms:
            if form.strip() and (form in q or form.replace(" ", "") in q_compact):
                return True
    return False


def _search_kind(parser_plan: dict, api_plan: dict | None, graph_plan: dict | None) -> str:
    if graph_plan:
        return _GRAPH_SEARCH_KIND
    if str(parser_plan.get("mode") or "") == "reporter":
        return _REPORT_SEARCH_KIND
    endpoint = str(
        (api_plan or {}).get("endpoint") or parser_plan.get("target_endpoint") or ""
    ).strip()
    if endpoint:
        normalized = "/" + endpoint.split("?", 1)[0].strip().strip("/") + "/"
        for known, phrase in _SEARCH_KIND_BY_ENDPOINT.items():
            if normalized == known or normalized.startswith(known.split("{", 1)[0]):
                return phrase
    return _GENERIC_SEARCH_KIND


def _clean_note(text: Any) -> str | None:
    note = " ".join(str(text or "").split())
    if not note:
        return None
    return note[:_NOTE_MAX_CHARS - 1] + "…" if len(note) > _NOTE_MAX_CHARS else note


def describe_query_scope(
    *,
    entity_result: dict,
    parser_plan: dict,
    api_plan: dict | None = None,
    graph_plan: dict | None = None,
    extra_notes: list[str] | None = None,
    user_query: str | None = None,
) -> QueryScope:
    """Split the turn's constraints into the ones the query carried and the rest.

    ``user_query``, when given, drops the assays, projects and keywords the question
    never mentions from what counts as asked for (see ``_asked_for``).
    """
    entity_result = entity_result if isinstance(entity_result, dict) else {}
    parser_plan = parser_plan if isinstance(parser_plan, dict) else {}

    scope = QueryScope(searched=_search_kind(parser_plan, api_plan, graph_plan))

    if graph_plan:
        note = _clean_note(graph_plan.get("explanation"))
        if note:
            scope.notes.append(note)
    for note in extra_notes or []:
        cleaned = _clean_note(note)
        if cleaned:
            scope.notes.append(cleaned)

    haystack = _executed_text(api_plan, graph_plan)
    if haystack is None:
        # No executed query text: report no verdict rather than a guessed one.
        return scope

    scope.measurable = True
    asked = _asked_for(entity_result, parser_plan, user_query)
    # A keyword the entity step also resolved to a sample type is constrained whenever that
    # type is: "mouse" is realised as the label T_MUS and appears nowhere as a word.
    type_by_name = {
        _folded(name).strip(): value
        for kind, value, _, name in asked if kind == "sample type" and name
    }
    for kind, value, label, name in asked:
        if kind == "sample type":
            applied = _type_is_applied(value, haystack)
        elif kind == "assay":
            applied = _assay_is_applied(value, name, haystack)
        elif kind == "keyword":
            applied = _keyword_is_applied(value, haystack)
            if not applied:
                code = type_by_name.get(_folded(value).strip())
                applied = bool(code) and _type_is_applied(code, haystack)
        else:
            applied = _is_applied(value, haystack)
        (scope.applied if applied else scope.not_applied).append(label)
    return scope


def render_query_scope(scope: QueryScope) -> str:
    """The prompt block. Entity codes, entity names and fixed phrases only."""
    lines = ["What the query actually did:", f"- Searched: {scope.searched}"]
    if scope.measurable:
        lines.append(
            "- Constrained by: " + ("; ".join(scope.applied) if scope.applied else "(nothing)")
        )
        if scope.not_applied:
            lines.append(
                "- NOT APPLIED, the user asked for this and the query did not constrain on it: "
                + "; ".join(scope.not_applied)
            )
    for note in scope.notes:
        lines.append(f"- Note from whoever built the query: {note}")
    return "\n".join(lines)

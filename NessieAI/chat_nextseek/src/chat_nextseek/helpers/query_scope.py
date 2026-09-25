"""A description of the query that ran, in words a researcher can read.

Decision D1. The chatter used to be told what the user asked for and what came back,
and nothing whatever about the query in between: no endpoint, no method, no
``requestBody``, no ``filter_searchText``, no Cypher, no parser filters, no
``intent_summary``. That was deliberate — the comment in ``agents/chatter.py`` says
the LLM never sees them — and the reason is sound: a biologist must not be answered
with retrieval mechanics.

The cost of it is three production failures out of 84 turns, all the same shape: the
reply described a result as if it answered a question the query never asked.

* **B7** (task 462) "list all the human patient samples (PAT) associated with
  MDL-250912LAU-1" fetched the whole lineage of the model and never filtered to PAT.
  The reply reported 1,904 matching records; none of the nine it showed were PAT.
* **B8** (task 437) searched ``D.FLOW`` and ``D.CYTOF`` and the reply named
  ``D.FCS``. The review's fix is "name types in the reply from the query that ran".
* **B13** (tasks 501/502) counted every mouse with transcriptomic descendants.
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
applied, so this under-reports the gap and never invents one. The one step containment
cannot see is a keyword the graph agent turned into a field ("positive" into
``QFT_Result``): the agent names that field in ``keyword_fields``, and the keyword counts as
applied only when the executed Cypher really filters on it. When nothing describes
an executed query at all — the reporter path has no query text — the scope reports
itself as unmeasurable and claims no gap, because "the query ignored your filter" is
exactly the kind of confident false statement this is here to prevent.

The 2026-09-23 runs showed seven more steps containment could not see, each a false
NOT APPLIED line over a correct query, and each now read from the query that ran:
a question that asks for samples with no type before a topic asks for no sample type
(R5-646, R5-678, R7-711); an assay counts when the query constrained a data type whose
name holds its title, "Imaging" and D.IMG "Imaging Data" (R5-653); a project or keyword
counts when the query compared a Project, Study or Investigation title that holds it or
that it holds, "Impact" and 'IMPAcTb' (R7-708, R6-1221, R6-1227); a word of a long
keyword matched that way is covered with it (R7-711); a keyword glossed by an applied
term, or glossing one, counts (R6-1227); a keyword word counts in its singular or
plural, or begun by a literal of four or more characters the query compared (R5-667,
R5-631); and a whole-type everyday name ("monkey") counts when that type's label is in
the query (R5-650, R5-670). Each of the seven only moves an item out of NOT APPLIED, or
out of what counts as asked for, never into it, so the rule above still holds.
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
    NHP without saying it. The one exception is a question that asks for samples with no
    type before a topic ("Find me all samples associated with X", "Show me samples
    processed via X"): it asks for every sample, so a type read out of X is not counted
    unless the question writes that type's code (``_EVERY_SAMPLE``).
    """
    filters = parser_plan.get("filters") or {}
    asked: list[tuple[str, str, str, str | None]] = []
    _guessable = {"assay", "project", "keyword"}
    every_sample = user_query is not None and bool(_EVERY_SAMPLE.match(user_query))

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
        if kind == "sample type" and every_sample and not _code_written(user_query, value):
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


#: A graph label (``:T_D_SEQ``), removed before ``_name_is_applied`` splits on underscores, so
#: a name never counts as applied because it is one segment of a sample type's label.
_GRAPH_LABEL = re.compile(r":\s*`?T_[A-Za-z0-9_]+`?")


def _name_is_applied(value: str, haystack: str) -> bool:
    """``_is_applied``, also reading ``_`` in the query's values as a word break.

    ``_`` is an identifier character, so the project or keyword ``SRP`` was never found in a
    query that scoped on ``'MIT_SRP'``: every MIT_SRP-scoped reply opened by saying the SRP
    scope was not applied (local run 2026-09-22, bucket3.a_mixed_result_is_not_named_after_one_type:
    "I could not apply the requested constraints for the SRP project or keyword" over a
    correct 57,441). For project and keyword names only; sample types keep the strict test
    (``test_a_label_counts_only_as_a_whole_label``), and graph labels are removed first.
    """
    if _is_applied(value, haystack):
        return True
    return _is_applied(value, _GRAPH_LABEL.sub(" ", haystack).replace("_", " "))


def _keyword_is_applied(keyword: str, haystack: str) -> bool:
    """A keyword counts as applied when it, or any of its words of three or more
    characters, is in the query: "RIN score" is constrained by ``s.RIN > 7``. Looser
    than the other kinds on purpose, in the direction this module errs in: it can miss
    a dropped keyword, never invent one."""
    return _name_is_applied(keyword, haystack) or _fragment_is_applied(keyword, haystack)


#: Properties every Sample carries. A keyword said to be realised as one of these was matched as
#: text (``search_text``) or not at all, so the declaration proves nothing about a field.
_SYSTEM_PROPERTIES = frozenset({
    "id", "uuid", "type", "title", "project_ids", "search_text", "synced_at", "source_hash",
    "parent_titles", "parent_title_hashes",
})


def _declared_fields(keyword: str, graph_plan: dict | None) -> list[str]:
    """The fields the graph agent says ``keyword`` became (``GraphAgentPlan.keyword_fields``)."""
    declared = (graph_plan or {}).get("keyword_fields")
    if not isinstance(declared, dict):
        return []
    key = _folded(keyword).strip()
    fields: list[str] = []
    for name, value in declared.items():
        if _folded(name).strip() != key:
            continue
        for field_name in [value] if isinstance(value, str) else (value if isinstance(value, list) else []):
            text = str(field_name or "").strip().strip("`")
            # "s.Classification" names the same field as "Classification".
            text = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*\.", "", text).strip("`")
            if text and text not in _SYSTEM_PROPERTIES and text not in fields:
                fields.append(text)
    return fields


def _field_is_filtered(field_name: str, cypher: str) -> bool:
    """Whether the Cypher reads ``field_name`` as a property before its last ``RETURN``.

    The field has to be written in the query (``s.QFT_Result``, ``s.`QuantiFERON-TB```,
    ``s['QFT_Result']``), with its exact case, as a property of some variable. Only the text
    before the last ``RETURN`` counts, so a field that is merely projected is not a
    constraint.
    """
    returns = list(re.finditer(r"\bRETURN\b", cypher, re.IGNORECASE))
    body = cypher[:returns[-1].start()] if returns else cypher
    escaped = re.escape(field_name)
    pattern = (
        r"(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]*\s*\.\s*(?:`" + escaped + r"`|" + escaped + r"(?![A-Za-z0-9_]))"
        r"|\[\s*['\"]" + escaped + r"['\"]\s*\]"
    )
    return re.search(pattern, body) is not None


def _keyword_realised_as_field(keyword: str, graph_plan: dict | None) -> bool:
    """A keyword the query constrained through a named field instead of by its own text.

    "Show samples for human subjects who convert to Mtb infection positive" (production,
    2026-09-23) answered 98 patients by ``Classification`` and the QuantiFERON-TB result, a
    correct query in which none of "Mtb", "infection" or "positive" appears as text, and the
    reply opened by saying the search could not be constrained by them. Containment cannot
    see that step, so the graph agent records it (``keyword_fields``), and it counts only when
    a declared field really is filtered in the executed query: a declaration the query does
    not bear out, or one naming a system property, changes nothing.
    """
    cypher = str((graph_plan or {}).get("cypher") or "")
    return bool(cypher) and any(_field_is_filtered(f, cypher) for f in _declared_fields(keyword, graph_plan))


def _project_title_is_applied(name: str, graph_plan: dict | None, haystack: str) -> bool:
    """Whether the query carries the ``Project.title`` the resolved project ``name`` is stored under.

    "Impact" is the catalog's name for the project whose title is "IMPAcTb", and a query scoped on
    that exact title never contains the word "Impact" as a word. The graph agent's code records the
    title it was given for each resolved project (``GraphAgentPlan.project_titles``, from the
    projects catalog's alternative names); the project counts as applied only when that title is
    itself in the executed query.
    """
    titles = (graph_plan or {}).get("project_titles")
    if not isinstance(titles, dict):
        return False
    key = _folded(name).strip()
    for resolved, title in titles.items():
        if _folded(resolved).strip() == key and isinstance(title, str) and title.strip():
            if _is_applied(title, haystack):
                return True
    return False


def _fragment_is_applied(value: str, haystack: str) -> bool:
    """Whether any word of ``value`` of three or more characters is in the query.

    The looser half of ``_keyword_is_applied``, lifted out so assays can use it too.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", value) if len(w) >= 3]
    return len(words) > 1 and any(_is_applied(w, haystack) for w in words)


#: A bare assay/type code such as ``A.TIS``: too short to carry meaning as a word.
_BARE_ASSAY_CODE = re.compile(r"^[A-Za-z]\.[A-Za-z0-9]+$")


def _data_type_segments(haystack: str) -> set[str]:
    """The words of every DATA-type label in the query.

    Only ``D.*`` and ``A.*`` types, written as ``T_D_FLOW`` / ``T_A_GEX``: those are the types
    an assay PRODUCES, which is what makes reaching one evidence that the assay was applied.
    A biological type is not -- ``MATCH (s:T_TIS)`` constrains the sample type to tissue and
    says nothing about a Tissue Collection assay.

    Segments, not a substring: collapsing the label and asking ``word in collapsed`` reported
    "Tissue Collection" as applied against ``T_TIS`` (``tis`` inside ``ttis``), and the same
    for RNA against ``T_RNA`` and DNA against ``T_DNA``. Three assays reported as constrained
    by a query that only constrained a sample type, which is the exact failure the caveat
    exists to prevent.
    """
    out: set[str] = set()
    for token in re.findall(r"T_[AD]_[A-Za-z0-9_]+|\b[AD]\.[A-Z0-9]+\b", haystack):
        parts = re.split(r"[_.]", token)
        out.update(part.lower() for part in parts[1:] if part and part not in ("A", "D"))
    return out


def _assay_is_applied(code: str, name: str | None, haystack: str,
                      applied_types: list[tuple[str, str | None]] | None = None) -> bool:
    """Whether the query constrained on an assay.

    An assay is asked for by its full title, and the query almost never carries that title
    verbatim. The graph agent is told to write a lowercased fragment of it, or to filter the
    edge property ``internal_assay_title``, or to reach the assay's data type by its label. An
    exact containment test sees none of those, so six correct answers in the 2026-09-18 runs
    opened by saying the assay had not been applied -- one of them over a Cypher that filtered
    ``internal_assay_title`` on exactly the right term.

    Three ways to count: the title or code itself, any substantial word of it, or a word of
    its TITLE naming a data type the query reached. The last is deliberately narrow, because
    a loose version of it invented applied assays: only ``D.*``/``A.*`` labels count, only
    whole segments match, and the short code is not a source of words for it.
    """
    for candidate in (code, name):
        if not candidate:
            continue
        if _is_applied(candidate, haystack) or _fragment_is_applied(candidate, haystack):
            return True
    # The title, which is what `code` also holds when the catalog gives no separate name
    # (`_codes_and_names` drops a name equal to its code, so `name` is often None here).
    # A bare assay code is excluded: those are the three-letter strings whose substring
    # match invented an applied assay in the first place.
    title = name or code
    segments = _data_type_segments(haystack)
    if segments and title and not _BARE_ASSAY_CODE.match(title):
        for word in re.split(r"[^A-Za-z0-9]+", title):
            if len(word) >= 3 and word.lower() not in _GENERIC_LAST_WORDS and word.lower() in segments:
                return True
    # A D.*/A.* type the query constrained whose name holds every word of the title: "Imaging" and D.IMG
    # "Imaging Data" (R5-653). "CometChip Assay" and "Imaging Mass Cytometry" are not held by "Imaging Data".
    wanted = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", title or "") if len(w) >= 3} - _GENERIC_LAST_WORDS
    for type_code, type_name in applied_types or []:
        if not wanted or not type_name or not re.match(r"^[AD]\.", type_code or ""):
            continue
        if wanted <= {w.lower() for w in re.split(r"[^A-Za-z0-9]+", type_name) if len(w) >= 3}:
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


# --------------------------------------------------------------------------- #
# Phase F (D2): what the 2026-09-23 runs showed containment could not see.
# Every route below only moves a constraint from NOT APPLIED to applied, or out
# of "asked for": the module's rule (under-report, never invent) still holds.
# --------------------------------------------------------------------------- #

#: A question that asks for samples with no type and then names a topic: "Find me all samples associated with X",
#: "What are the samples associated with this paper: ...", "Show me samples processed via X". graph_agent.txt reads
#: it as every sample ("when it asks for 'samples' or 'all samples' with no type, search every sample"), so a type
#: the entity step read out of X is not asked for (R5-646, R5-678, R7-711).
_EVERY_SAMPLE = re.compile(
    r"^\W*(?:(?:please|can you|could you|would you)\s+)?"
    r"(?:(?:find|show|list|get|give|return|fetch|pull|display|what are|which are|what|which|how many)\s+)?"
    r"(?:(?:me|to me|us)\s+)?(?:(?:all|every|any)\s+)?(?:(?:of\s+)?the\s+)?samples?\s+"
    r"(?:(?:that\s+)?(?:are|were|have\s+been)\s+)?"
    r"(?:associated\s+with|related\s+to|linked\s+to|mentioning|processed\s+(?:via|by|with|through))\b",
    re.IGNORECASE,
)


def _code_written(question: str, code: str) -> bool:
    """The question writes the type's code itself ("samples of type AB")."""
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])", question or "") is not None


#: Whole-type everyday names, from entity_agent.txt's own examples ("mouse/mice/murine -> MUS, NHP/non-human
#: primate/macaque/monkey -> NHP, tissue -> TIS, cell -> CEL") minus "macaque", a genus inside NHP. Never a narrowing
#: term: the catalog Tags also list CC and rhesus macaque, and reading those as the type would hide B13's gap.
_COMMON_TYPE_NAMES = {
    "monkey": "NHP", "primate": "NHP", "non human primate": "NHP", "nonhuman primate": "NHP",
    "mouse": "MUS", "mice": "MUS", "murine": "MUS",
    "tissue": "TIS", "cell": "CEL",
}


def _singular(text: str) -> str:
    """Lowercased words joined by one space, one trailing "s" dropped (not "ss"): "Monkeys" is "monkey"."""
    t = " ".join(re.split(r"[^a-z0-9]+", str(text or "").lower())).strip()
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t


def _common_name_is_applied(keyword: str, haystack: str) -> bool:
    """R5-650, R5-670: "monkey" kept as a keyword, and the query constrained T_NHP."""
    code = _COMMON_TYPE_NAMES.get(_singular(keyword))
    return bool(code) and _type_is_applied(code, haystack)


def _literals(graph_plan: dict | None) -> list[str]:
    """Single-token strings of 4+ characters the executed Cypher compares: quoted literals and string parameters."""
    out: list[str] = []
    for a, b in re.findall(r"'([^'\\]*)'|\"([^\"\\]*)\"", str((graph_plan or {}).get("cypher") or "")):
        out.append(a or b)

    def walk(value: Any) -> None:
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    for value in ((graph_plan or {}).get("parameters") or {}).values():
        walk(value)
    return [v for v in out if re.fullmatch(r"[A-Za-z0-9]{4,}", v or "")]


def _word_forms_applied(keyword: str, haystack: str, literals: list[str]) -> bool:
    """Any word of the keyword in its singular or plural ("SOP" in /sops/, R5-667; "attributes" against
    :Attribute, R5-631), or begun by a literal the query compared ('vocab' for "vocabulary", R5-631). Words under
    three characters never widen, so "CC" keeps the strict test (B13)."""
    bare = _GRAPH_LABEL.sub(" ", haystack)
    for word in re.split(r"[^A-Za-z0-9]+", keyword):
        if len(word) < 3:
            continue
        stem = _singular(word)
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(stem) + r"(?:s|es)?(?![A-Za-z0-9_])", bare, re.IGNORECASE):
            return True
        if len(word) >= 5 and any(word.lower().startswith(v.lower()) and len(v) < len(word) for v in literals):
            return True
    return False


def _squash(text: Any) -> str:
    """Lowercase letters and digits only: "IMPAcTb", "impactb" and "IMPAc-Tb" are one string."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


_CONTAINER_VAR = re.compile(r"\(\s*(\w+)\s*:\s*`?(?:Project|Study|Investigation)`?\b")
_CONTAINER_MAP = re.compile(
    r"\(\s*\w*\s*:\s*`?(?:Project|Study|Investigation)`?\s*\{[^}]*\btitle\s*:\s*(\$\w+|'[^']*'|\"[^\"]*\")")
_TITLE_COMPARED = re.compile(
    r"(?:toLower\(\s*)?(\w+)\.title\s*\)?\s*(?:=|IN|CONTAINS|STARTS\s+WITH|ENDS\s+WITH)\s*(?:toLower\(\s*)?"
    r"(\$\w+|'[^']*'|\"[^\"]*\"|\[[^\]]*\])", re.IGNORECASE)


def _container_titles(graph_plan: dict | None) -> list[str]:
    """The values the executed Cypher compares a Project, Study or Investigation title to. A Sample's own title
    is not one: ``s.title CONTAINS`` is a text match."""
    cypher = str((graph_plan or {}).get("cypher") or "")
    params = (graph_plan or {}).get("parameters") or {}
    containers = {m.group(1) for m in _CONTAINER_VAR.finditer(cypher)}
    tokens = [m.group(1) for m in _CONTAINER_MAP.finditer(cypher)]
    tokens += [m.group(2) for m in _TITLE_COMPARED.finditer(cypher) if m.group(1) in containers]
    titles: list[str] = []
    for token in tokens:
        if token.startswith("$"):
            value = params.get(token[1:])
            titles += [v for v in (value if isinstance(value, list) else [value]) if isinstance(v, str)]
        elif token.startswith("["):
            titles += [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", token)]
        else:
            titles.append(token[1:-1])
    return [t for t in titles if len(_squash(t)) >= 3]


def _container_title_is_applied(value: str, titles: list[str]) -> bool:
    """The query scoped a project, study or investigation by a title that holds the asked name, or that the asked
    name holds: "Impact" and 'IMPAcTb' (R7-708); "TCGA LUAD" and 'LUAD' under 'TCGA' (R6-1227, R6-1221)."""
    key = _squash(value)
    return len(key) >= 3 and any(key in _squash(t) or _squash(t) in key for t in titles)


def _glossed_by_applied(keyword: str, question: str | None, haystack: str) -> bool:
    """A keyword the question writes as the gloss of an applied term, or glosses with one: "LUAD (lung
    adenocarcinoma)" (R6-1227)."""
    key = _squash(keyword)
    for m in re.finditer(r"([A-Za-z0-9][\w.\-]*)\s*\(\s*([^()]{2,80}?)\s*\)", question or ""):
        outer, inner = m.group(1), m.group(2)
        if (key == _squash(inner) and _is_applied(outer, haystack)) or (
                key == _squash(outer) and _is_applied(inner, haystack)):
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
    applied_types = [(value, name) for kind, value, _, name in asked
                     if kind == "sample type" and _type_is_applied(value, haystack)]
    titles = _container_titles(graph_plan)
    literals = _literals(graph_plan)
    # A long keyword (a paper or study title) the query matched as a title covers the words inside it (R7-711).
    title_phrases = [_squash(value) for kind, value, _, _ in asked
                     if kind == "keyword" and len(value.split()) >= 3 and _container_title_is_applied(value, titles)]
    for kind, value, label, name in asked:
        if kind == "sample type":
            applied = _type_is_applied(value, haystack)
        elif kind == "assay":
            applied = _assay_is_applied(value, name, haystack, applied_types)
        elif kind == "keyword":
            applied = _keyword_is_applied(value, haystack)
            if not applied:
                applied = _word_forms_applied(value, haystack, literals)
            if not applied:
                applied = _common_name_is_applied(value, haystack)
            if not applied:
                code = type_by_name.get(_folded(value).strip())
                applied = bool(code) and _type_is_applied(code, haystack)
            if not applied:
                applied = _keyword_realised_as_field(value, graph_plan)
            if not applied:
                # The entity step also copies a project's name into the keywords ("Impact").
                applied = _project_title_is_applied(value, graph_plan, haystack)
            if not applied:
                applied = _container_title_is_applied(value, titles)
            if not applied:
                applied = _glossed_by_applied(value, user_query, haystack)
            if not applied:
                key = _squash(value)
                applied = bool(key) and any(key in phrase and key != phrase for phrase in title_phrases)
        elif kind == "project":
            applied = _name_is_applied(value, haystack)
            if not applied:
                applied = _project_title_is_applied(value, graph_plan, haystack)
            if not applied:
                applied = _container_title_is_applied(value, titles)
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

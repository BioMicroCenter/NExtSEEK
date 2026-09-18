"""Build the Cypher for one graph_search request.

Pure: the request's filters, the caller's ``Scope`` and the graph ``Catalog`` in; three Cypher statements and one
parameter map out. No database, no settings. The graph is schema v1.1 (docs/neo4j-schema.md) and the rules are the
design's matching table (docs/superpowers/specs/2026-09-14-graph-search-poc-design.md, section 7).

The fields shared with advanced_search keep its matching, taken from its engine
(seek/sample/search.py::SampleSearchMixin._filterSamples_advanced and the SQL it runs) and from the attribute stage of
nextseek_api/services/samples.py::SampleAdvancedSearchViewSet.create:

- Terms are split as the view splits them. A term matching ``UID_RE`` is an exact ``uuid`` match, unioned with the
  text results whatever ``searchText_logic`` says, as the view runs the two as separate searches and unions the rows.
- Stage 1 (the engine), text terms only. SQL ANDs or ORs ``json_metadata LIKE %term%`` per term; Python then keeps a
  row when ANY term matches ANY value: a case-insensitive substring for PARTIAL, a case-insensitive, untrimmed
  equality for EXACT. Over ``search_text`` (values only, one per line) that is: PARTIAL, each term contained, combined
  by the logic; EXACT OR, any term equal to a value; EXACT AND, every term contained and any term equal to a value.
- Stage 2 (the view), only when ``attribute`` names and terms are both given, UID terms included. Each name resolves
  to every catalog title whose stripped lowercase equals it (advanced_search strips and lowercases metadata keys);
  PARTIAL is a lowercase substring of the value, EXACT a lowercase equality after stripping the value with Python's
  whitespace set (``btrim(..., $ws)``, because Cypher's ``trim`` keeps a no-break space); names are combined by
  ``attribute_logic`` (the first name alone when it is unset), terms by ``searchText_logic``.

``extensions`` are graph_search's own: exact, typed, case-sensitive conditions, validated against the catalog. The
string operators compare the value's text (``toString``), as advanced_search's Contain compared ``str(value)``.

``extensions.query`` is the Sample Search page's query text (``text_query`` parses it), matched with advanced_search's
two stages (this package's README, "How graph_search expresses them"): each term holds when the sample's JSON text holds
it, which the graph reads as a value in ``search_text`` or, through the catalog, a key name of the sample's type; the
terms combine as the text says, a tag adds the sample type outside any negation; then one of the text's positive terms
must be in (PARTIAL) or equal to (EXACT) one of the values.

Every value is a parameter. The only text interpolated into Cypher is a catalog title or label (backtick-quoted,
backticks doubled), an operator from a fixed set, and a hop count checked to be an integer from 1 to 4.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from nextseek_api.batch_upload.helpers import UID_RE
from nextseek_api.graph_search import lucene, text_query
from nextseek_api.graph_search.scope import Scope
from nextseek_api.graph_sync.projection import SKIPPED_METADATA_KEYS, cast_value

log = logging.getLogger(__name__)

FULLTEXT_INDEX = "sample_search_text"
MAX_PAGE_SIZE = 1000
MAX_HOPS = 4
WHERE_OPS = frozenset({"=", "<>", "<", "<=", ">", ">=", "IN", "CONTAINS", "NOT CONTAINS", "STARTS WITH",
                       "IS TRUE", "IS FALSE"})
_STRING_OPS = frozenset({"CONTAINS", "NOT CONTAINS", "STARTS WITH"})
_TRUTH_OPS = frozenset({"IS TRUE", "IS FALSE"})
# The text of an integer that Python's int() reads as 1, after strip(): an optional plus, zeros with single
# underscores between digits, then 1. No backslash, so it needs no escaping inside a Cypher string.
_ONE_RE = "[+]?(0_?)*1"
_LINEAGE_PATTERNS = {
    "descendant": "EXISTS {{ (s)<-[:DERIVED_FROM*1..{hops}]-(:{label}) }}",
    "ancestor": "EXISTS {{ (s)-[:DERIVED_FROM*1..{hops}]->(:{label}) }}",
}
_INT64_MIN, _INT64_MAX = -(2 ** 63), 2 ** 63 - 1

_FULLTEXT_SOURCE = f"CALL db.index.fulltext.queryNodes('{FULLTEXT_INDEX}', $lucene) YIELD node AS s"
_UID_SOURCE = "MATCH (s:Sample) WHERE s.uuid IN $uids"
_TYPE_SOURCE = "MATCH (s:Sample) WHERE s.type IN $types"
_ALL_SOURCE = "MATCH (s:Sample)"
_QUERY_TYPE_SOURCE = "MATCH (s:Sample) WHERE s.type IN $query_types"

_UID_MATCH = "s.uuid IN $uids"
_TYPES_MATCH = "s.type IN $types"
_SCOPE_MATCH = "any(p IN s.project_ids WHERE p IN $projects)"

# Every character Python's str.strip() removes (str.isspace()). advanced_search's EXACT attribute match strips values
# with str.strip(); Cypher's trim() keeps some of these (a no-break space, for one), so the builder passes this set
# to btrim() instead.
PY_WHITESPACE = (
    "\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0 "
    "           "
    "    　"
)

_NOTHING_TO_SEARCH = (
    "Give a filter_searchText, a sampletype that exists on this instance, extensions.where or extensions.query. "
    "A search with none of them would read every sample."
)


@dataclass(frozen=True)
class Catalog:
    """What the graph says types and attributes are; filled from the graph by ``catalog_cache.get_catalog``."""

    type_title_by_id: dict[int, str]
    label_by_title: dict[str, str]
    titles_by_type: dict[str, frozenset[str]]        # sample type title -> attribute titles
    value_type: dict[tuple[str, str], str]           # (type title, attribute title) -> value_type


class GraphSearchInvalid(ValueError):
    """A request the catalog rejects; the view answers 422 with str(exc)."""


@dataclass(frozen=True)
class BuiltQuery:
    page_cypher: str
    count_cypher: str
    ids_cypher: str          # every matching id, no paging (parity harness)
    params: dict


def quote_name(name: str) -> str:
    """A Cypher identifier: backtick-quoted, any backtick doubled."""
    return "`" + str(name).replace("`", "``") + "`"


def split_terms(raw) -> list[str]:
    """Search terms exactly as advanced_search's view splits them: list or single string, stripped, empties dropped."""
    if isinstance(raw, (list, tuple)):
        return [str(t or "").strip() for t in raw if str(t or "").strip()]
    text = str(raw or "").strip()
    return [text] if text else []


def _get(obj, name: str, default=None):
    """Read a field from an extensions model or from the same shape as a plain dict (the parity harness)."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _prop(title: str) -> str:
    """The node property holding an attribute. ``UID`` is not written (it equals ``uuid``), so it reads ``uuid``."""
    if title in SKIPPED_METADATA_KEYS:
        return "s.uuid"
    return "s." + quote_name(title)


def _join(parts: list[str], logic: str) -> str:
    if len(parts) == 1:
        return parts[0]
    return "(" + f" {logic} ".join(parts) + ")"


def _logic(value) -> str:
    """advanced_search: AND only when the request says AND; OR otherwise."""
    return "AND" if str(value or "OR").upper() == "AND" else "OR"


def _type_titles(filters: dict, catalog: Catalog) -> Optional[list[str]]:
    """Titles of the requested type ids, request order, unknown ids dropped. ``None`` when no type was requested.

    An empty list (every id unknown) still filters, to nothing, as advanced_search's SQL does.
    """
    ids = filters.get("sampletype_ids") or []
    if not ids:
        return None
    titles: list[str] = []
    for raw in ids:
        try:
            title = catalog.type_title_by_id.get(int(str(raw)))
        except (TypeError, ValueError):
            continue
        if title is not None and title not in titles:
            titles.append(title)
    return titles


def _text_stage(indices: list[int], logic: str, exact: bool) -> str:
    contains = [f"toLower(s.search_text) CONTAINS $t{i}" for i in indices]
    if not exact:
        return _join(contains, logic)
    equals = [f"$t{i} IN split(toLower(s.search_text), '\\n')" for i in indices]
    if logic == "AND" and len(indices) > 1:
        return "(" + _join(contains, "AND") + " AND " + _join(equals, "OR") + ")"
    return _join(equals, "OR")


def _attribute_names(filters: dict) -> list[str]:
    attr_list = filters.get("attribute_list") or []
    if not isinstance(attr_list, list):
        return []
    return [str(a).strip().lower() for a in attr_list if isinstance(a, str) and str(a).strip()]


def _attribute_stage(names: list[str], attr_logic, n_terms: int, logic: str, exact: bool,
                     types: Optional[list[str]], catalog: Catalog) -> str:
    if types is None:
        pool = set().union(*catalog.titles_by_type.values()) if catalog.titles_by_type else set()
    else:
        pool = set().union(*(catalog.titles_by_type.get(t, frozenset()) for t in types)) if types else set()
    variants = {name: sorted(t for t in pool if t.strip().lower() == name) for name in names}

    def one(title: str, i: int) -> str:
        value = f"toLower(toString({_prop(title)}))"
        return f"btrim({value}, $ws) = $t{i}" if exact else f"{value} CONTAINS $t{i}"

    def name_matches(name: str, i: int) -> str:
        titles = variants[name]
        return _join([one(t, i) for t in titles], "OR") if titles else "false"

    if attr_logic == "AND":
        per_term = [_join([name_matches(n, i) for n in names], "AND") for i in range(n_terms)]
    elif attr_logic == "OR":
        per_term = [_join([name_matches(n, i) for n in names], "OR") for i in range(n_terms)]
    else:
        per_term = [name_matches(names[0], i) for i in range(n_terms)]
    return _join(per_term, logic)


def _check_int64(value) -> None:
    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, int) and not isinstance(item, bool) and not _INT64_MIN <= item <= _INT64_MAX:
            raise GraphSearchInvalid("where: an integer value is outside the 64-bit range")


def _where_value(value, value_type: str, op: str):
    if op in _STRING_OPS:
        return value if isinstance(value, str) else str(value)
    if op == "IN":
        return [cast_value(v, value_type)[0] for v in value]
    return cast_value(value, value_type)[0]


def _truthy(prop: str) -> str:
    """advanced_search's True rule, ``toBinaryTinyInt(value) == 1`` (dmac/conversion.py), over a stored value.

    ``int(value)`` is 1 for a boolean true, the integer 1, a float that truncates to 1 and a string ``int()`` reads as
    1 (Python whitespace trimmed); otherwise the trimmed, lower-cased text must be ``true`` or ``yes``. A date, a
    missing value or anything else is not true. Uses ``$ws``.
    """
    return (
        f"CASE WHEN {prop} IS :: BOOLEAN NOT NULL THEN {prop} "
        f"WHEN {prop} IS :: INTEGER NOT NULL THEN {prop} = 1 "
        f"WHEN {prop} IS :: FLOAT NOT NULL THEN {prop} >= 1.0 AND {prop} < 2.0 "
        f"WHEN {prop} IS :: STRING NOT NULL THEN btrim({prop}, $ws) =~ '{_ONE_RE}' "
        f"OR toLower(btrim({prop}, $ws)) IN ['true', 'yes'] "
        "ELSE false END"
    )


def _where(items: list, catalog: Catalog, params: dict) -> tuple[Optional[str], list[str]]:
    """The where items' label and predicates. Items are ANDed and must all name the same sample type."""
    if not items:
        return None, []
    types = {str(_get(item, "sample_type")) for item in items}
    if len(types) > 1:
        raise GraphSearchInvalid("every where item must name the same sample_type")
    sample_type = types.pop()
    label = catalog.label_by_title.get(sample_type)
    if label is None:
        raise GraphSearchInvalid(f"where: unknown sample_type {sample_type!r}")
    titles = catalog.titles_by_type.get(sample_type, frozenset())
    predicates = []
    for i, item in enumerate(items):
        attribute, op, value = _get(item, "attribute"), _get(item, "op"), _get(item, "value")
        if attribute not in titles:
            raise GraphSearchInvalid(f"where: attribute {attribute!r} does not exist on sample type {sample_type!r}")
        if op not in WHERE_OPS:
            raise GraphSearchInvalid(f"where: unsupported op {op!r}")
        prop = _prop(attribute)
        if op in _TRUTH_OPS:
            # The Simple box's True and False rules. False is every other value the sample holds: advanced_search kept
            # only rows whose metadata has the attribute with a non-null value.
            if value is not None:
                raise GraphSearchInvalid(f"where: op {op!r} takes no value")
            params["ws"] = PY_WHITESPACE
            truthy = _truthy(prop)
            predicates.append(truthy if op == "IS TRUE" else f"({prop} IS NOT NULL AND NOT ({truthy}))")
            continue
        if value is None:
            raise GraphSearchInvalid(f"where: op {op!r} needs a value")
        if (op == "IN") != isinstance(value, list):
            raise GraphSearchInvalid(f"where: op {op!r} needs {'a list' if op == 'IN' else 'a single'} value")
        cast = _where_value(value, catalog.value_type.get((sample_type, attribute), "string"), op)
        _check_int64(cast)
        params[f"w{i}"] = cast
        if op == "NOT CONTAINS":
            # The Simple box's Not Contain: the negation of CONTAINS, over samples that hold the attribute.
            predicates.append(f"({prop} IS NOT NULL AND NOT (toString({prop}) CONTAINS $w{i}))")
        elif op in _STRING_OPS:
            # A string operator reads the value's text: a string attribute can hold a JSON number, which the graph
            # keeps as a number, and advanced_search's Contain compared str(value).
            predicates.append(f"toString({prop}) {op} $w{i}")
        else:
            predicates.append(f"{prop} {op} $w{i}")
    return label, predicates


def _lineage(lineage, catalog: Catalog) -> Optional[str]:
    if lineage is None:
        return None
    direction, sample_type = _get(lineage, "direction"), _get(lineage, "sample_type")
    hops = _get(lineage, "max_hops", MAX_HOPS)
    pattern = _LINEAGE_PATTERNS.get(direction)
    if pattern is None:
        raise GraphSearchInvalid(f"lineage: unsupported direction {direction!r}")
    if isinstance(hops, bool) or not isinstance(hops, int) or not 1 <= hops <= MAX_HOPS:
        raise GraphSearchInvalid(f"lineage: max_hops must be an integer from 1 to {MAX_HOPS}")
    label = catalog.label_by_title.get(sample_type)
    if label is None:
        raise GraphSearchInvalid(f"lineage: unknown sample_type {sample_type!r}")
    return pattern.format(hops=int(hops), label=quote_name(label))


def _tag_title(tag: str, catalog: Catalog) -> Optional[str]:
    """The sample type a ``term[TYPE]`` tag names, looked up as advanced_search looked it up, or None.

    ``DBtable_sampletype.getSampleTypeID`` cuts the (upper-cased) tag at its first ``_``, and ``getPrimarykey`` wants
    exactly one title equal to it under MySQL's case-insensitive, trailing-space-insensitive collation; empty and
    ``NA`` are refused. Anything else is id -1, which matches nothing.
    """
    code = tag.split("_")[0].strip().upper()
    if not code or code == "NA":
        return None
    titles = set(catalog.titles_by_type) | set(catalog.label_by_title) | set(catalog.type_title_by_id.values())
    found = [title for title in titles if title.rstrip(" ").upper() == code]
    return found[0] if len(found) == 1 else None


def _key_types(text: str, catalog: Catalog) -> list[str]:
    """The sample types with an attribute title holding ``text`` (case-insensitive): SEEK writes every declared key into
    ``json_metadata``, so advanced_search's LIKE found the term in each of their samples."""
    needle = text.lower()
    return sorted(t for t, titles in catalog.titles_by_type.items() if any(needle in title.lower() for title in titles))


@dataclass(frozen=True)
class _QueryStage:
    predicate: str
    candidates: Optional[str]            # a fulltext query every match satisfies, or None
    types: Optional[list[str]]           # sample types every match has, or None when the text does not bound them


def _query_stage(text: str, exact: bool, catalog: Catalog, params: dict) -> _QueryStage:
    """``extensions.query`` as one predicate, its fulltext candidates and the sample types it is bounded to."""
    try:
        tree = text_query.parse(text)
    except text_query.QueryTextInvalid as exc:
        raise GraphSearchInvalid(f"query: {exc}") from None
    leaves = text_query.terms(tree)
    titles: dict[int, Optional[str]] = {}
    keyed: set[int] = set()
    for leaf in leaves:
        i = leaf.index
        if leaf.tag is not None:
            titles[i] = _tag_title(leaf.tag, catalog)
            if titles[i] is not None:
                params[f"qt{i}"] = titles[i]
        if leaf.text:
            params[f"q{i}"] = leaf.text.lower()
            keys = _key_types(leaf.text, catalog)
            if keys:
                keyed.add(i)
                params[f"qk{i}"] = keys

    def void(leaf) -> bool:
        """A tag that names no sample type: advanced_search's id -1 made the term, negated or not, match nothing."""
        return leaf.tag is not None and titles[leaf.index] is None

    def holds(leaf) -> list[str]:
        parts = [f"toLower(s.search_text) CONTAINS $q{leaf.index}"]
        if leaf.index in keyed:
            parts.append(f"s.type IN $qk{leaf.index}")
        return parts

    def of_type(leaf) -> list[str]:
        return [f"s.type = $qt{leaf.index}"] if leaf.tag is not None else []

    def compile_(node) -> str:
        if isinstance(node, text_query.Term):
            if void(node):
                return "false"
            parts = ([_join(holds(node), "OR")] if node.text else []) + of_type(node)
            return _join(parts, "AND") if parts else "true"
        if isinstance(node, text_query.Not):
            inner = node.operand
            if isinstance(inner, text_query.Term):
                # The tag stays outside the negation: `NOT LIKE ... AND sample_type_id = ...`. Every JSON text holds
                # the empty term, so negating it (`NOT [TIS]`) matches nothing.
                if void(inner) or not inner.text:
                    return "false"
                return _join([f"NOT ({' OR '.join(holds(inner))})"] + of_type(inner), "AND")
            return f"NOT ({compile_(inner)})"
        joined = [compile_(operand) for operand in node.operands]
        return _join(joined, "AND" if isinstance(node, text_query.All) else "OR")

    def positive(node, negated=False) -> list:
        """The terms under an even number of NOTs, with text: those advanced_search's value stage could match."""
        if isinstance(node, text_query.Term):
            return [] if negated or not node.text else [node]
        if isinstance(node, text_query.Not):
            return positive(node.operand, not negated)
        return [leaf for operand in node.operands for leaf in positive(operand, negated)]

    def implies_value(node) -> bool:
        """Whether the combined terms can hold only through a positive term found in a value (PARTIAL)."""
        if isinstance(node, text_query.Term):
            return void(node) or (bool(node.text) and node.index not in keyed)
        if isinstance(node, text_query.Not):
            return False
        results = [implies_value(operand) for operand in node.operands]
        return any(results) if isinstance(node, text_query.All) else all(results)

    def candidates(node) -> Optional[str]:
        if isinstance(node, text_query.Term):
            if void(node) or not node.text or node.index in keyed:
                return None
            return lucene.candidate_query(node.text)
        if isinstance(node, text_query.Not):
            return None
        found = [candidates(operand) for operand in node.operands]
        if isinstance(node, text_query.All):
            found = [c for c in found if c is not None]
        elif any(c is None for c in found):
            return None
        if not found:
            return None
        logic = " AND " if isinstance(node, text_query.All) else " OR "
        return found[0] if len(found) == 1 else logic.join(f"({c})" for c in found)

    def bound(node) -> Optional[frozenset]:
        if isinstance(node, text_query.Not) and isinstance(node.operand, text_query.Term):
            node = node.operand
        if isinstance(node, text_query.Term):
            if node.tag is None:
                return None
            return frozenset() if void(node) else frozenset({titles[node.index]})
        if isinstance(node, text_query.Not):
            return None
        found = [bound(operand) for operand in node.operands]
        if isinstance(node, text_query.All):
            known = [b for b in found if b is not None]
            return frozenset.intersection(*known) if known else None
        return None if any(b is None for b in found) else frozenset().union(*found)

    predicate = compile_(tree)
    values = positive(tree)
    if values and (exact or not implies_value(tree)):
        if exact:
            stage = [f"$q{leaf.index} IN split(toLower(s.search_text), '\\n')" for leaf in values]
        else:
            stage = [f"toLower(s.search_text) CONTAINS $q{leaf.index}" for leaf in values]
        predicate = _join([predicate, _join(stage, "OR")], "AND")
    found = candidates(tree)
    if found is None and values:
        # The value stage needs one positive term in a value, so their candidates, ORed, cover every match.
        each = [lucene.candidate_query(leaf.text) for leaf in values]
        if all(c is not None for c in each):
            found = each[0] if len(each) == 1 else " OR ".join(f"({c})" for c in each)
    types = bound(tree)
    return _QueryStage(predicate, found, None if types is None else sorted(types))


def build(filters: dict, extensions, scope: Scope, catalog: Catalog, page: int, page_size: int) -> BuiltQuery:
    """Turn ``SampleAdvancedSearchRequest.to_db_filters(...)``'s output, ``extensions`` and scope into Cypher.

    ``page`` is 1-based; ``page_size`` above ``MAX_PAGE_SIZE`` is capped. Raises ``GraphSearchInvalid`` for anything the
    catalog rejects, a non-positive page or page size, or a request with nothing to search on.
    """
    if isinstance(page, bool) or isinstance(page_size, bool) or int(page) < 1 or int(page_size) < 1:
        raise GraphSearchInvalid("page and page_size must be positive integers")
    limit = min(int(page_size), MAX_PAGE_SIZE)
    skip = min((int(page) - 1) * limit, _INT64_MAX)

    params: dict[str, Any] = {}
    terms = split_terms(filters.get("filter_searchText"))
    uid_idx = [i for i, t in enumerate(terms) if UID_RE.match(t)]
    text_idx = [i for i, t in enumerate(terms) if not UID_RE.match(t)]
    logic = _logic(filters.get("searchText_logic"))
    exact = str(filters.get("filter_matchType") or "PARTIAL").upper() == "EXACT"
    types = _type_titles(filters, catalog)
    where_label, where_predicates = _where(list(_get(extensions, "where", None) or []), catalog, params)
    lineage_predicate = _lineage(_get(extensions, "lineage", None), catalog)
    query_text = _get(extensions, "query", None)
    query = None
    if query_text is not None and str(query_text).strip():
        query = _query_stage(str(query_text), exact, catalog, params)

    if not terms and types is None and where_label is None and query is None:
        raise GraphSearchInvalid(_NOTHING_TO_SEARCH)

    # Candidate source, most selective first.
    label_source = f"MATCH (s:{quote_name(where_label)})" if where_label else None
    scan = label_source or (_TYPE_SOURCE if types is not None else None)
    if scan is None and query is not None and query.types is not None:
        scan = _QUERY_TYPE_SOURCE
        params["query_types"] = query.types
    text_lucene = None
    if text_idx:
        queries = [lucene.candidate_query(terms[i]) for i in text_idx]
        usable = [q for q in queries if q is not None]
        # OR needs every term's candidates; AND is narrowed by any one term's.
        if usable and (logic == "AND" or len(usable) == len(queries)):
            text_lucene = usable[0] if len(usable) == 1 else f" {logic} ".join(f"({q})" for q in usable)
    query_lucene = query.candidates if query is not None else None
    if uid_idx and not text_idx:
        source = _UID_SOURCE
    elif text_lucene or query_lucene:
        # Both are supersets of the matches, so their intersection is too.
        params["lucene"] = (f"({text_lucene}) AND ({query_lucene})" if text_lucene and query_lucene
                            else text_lucene or query_lucene)
        source = _FULLTEXT_SOURCE
        if uid_idx and text_lucene:
            source = f"CALL () {{ {_FULLTEXT_SOURCE} RETURN s UNION {_UID_SOURCE} RETURN s }}"
    elif scan is not None:
        source = scan
    else:
        source = _ALL_SOURCE
        log.warning("graph_search: no term has a fulltext candidate query and no type narrows it; full scan")
    if source != _QUERY_TYPE_SOURCE:
        params.pop("query_types", None)

    predicates: list[str] = []
    if types is not None:
        params["types"] = types
        if source != _TYPE_SOURCE:
            predicates.append(_TYPES_MATCH)
    if not scope.is_admin:
        params["projects"] = list(scope.project_ids)
        predicates.append(_SCOPE_MATCH)
    if where_label and source != label_source:
        predicates.append(f"s:{quote_name(where_label)}")

    if uid_idx:
        params["uids"] = [terms[i] for i in uid_idx]
    for i in text_idx:
        params[f"t{i}"] = terms[i].lower()
    if text_idx and uid_idx:
        predicates.append(f"({_UID_MATCH} OR {_text_stage(text_idx, logic, exact)})")
    elif text_idx:
        predicates.append(_text_stage(text_idx, logic, exact))
    elif uid_idx and source != _UID_SOURCE:
        predicates.append(_UID_MATCH)

    names = _attribute_names(filters)
    if names and terms:
        for i in uid_idx:
            params[f"t{i}"] = terms[i].lower()
        attr_logic = filters.get("attribute_logic") or ("OR" if len(names) > 1 else None)
        stage = _attribute_stage(names, attr_logic, len(terms), logic, exact, types, catalog)
        if "$ws" in stage:
            params["ws"] = PY_WHITESPACE
        predicates.append(stage)

    if query is not None:
        predicates.append(query.predicate)
    predicates.extend(where_predicates)
    if lineage_predicate:
        predicates.append(lineage_predicate)

    lines = ["CYPHER 25", source]
    if predicates:
        lines.append("WITH s WHERE " + " AND ".join(predicates))
    body = "\n".join(lines)
    params["skip"] = skip
    params["limit"] = limit
    return BuiltQuery(
        page_cypher=body + "\nWITH s ORDER BY s.id SKIP $skip LIMIT $limit RETURN s.id AS id",
        count_cypher=body + "\nRETURN count(s) AS total, collect(DISTINCT s.type) AS types",
        ids_cypher=body + "\nRETURN s.id AS id ORDER BY id",
        params=params,
    )

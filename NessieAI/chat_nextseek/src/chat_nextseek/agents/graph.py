from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from .. import cypher_text, graph_catalog, graph_context
from ..config import ChatConfig
from ..schemas.schema_helper import call_llm_structured
from ..schemas import (
    EntityAgentOutput,
    GraphAgentPlan,
    ParserPlan,
)


# Matches `<ident>.<Prop>` property reads (e.g. s.Lab). Cypher functions like
# toLower(...) are matched only on their property argument, not the function name.
# Best-effort safety net: assumes simple `var.prop` access only — it does NOT parse
# map literals, `$param.x`, apoc procedure calls, or backtick-quoted props, so if such
# patterns are added to the graph prompt the guard may need extending.
# (This is the fallback guard. With the v1.1 catalog live, catalog_unknown_properties
# below checks names per label, backticked names and map projections included.)
_CYPHER_PROP_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.([A-Za-z_][A-Za-z0-9_]*)")


def known_node_properties(schema: dict) -> set[str]:
    """Union of every node property name across all labels in the graph schema."""
    props: set[str] = set()
    node_props = (schema or {}).get("node_properties") or {}
    if isinstance(node_props, dict):
        for plist in node_props.values():
            if isinstance(plist, list):
                props.update(str(p) for p in plist)
    return props


def known_relationship_properties(schema: dict) -> set[str]:
    """Union of every relationship property name across all relationship types
    in the graph schema (e.g. DERIVED_FROM.internal_assay_title). These are valid
    Cypher property reads on relationship variables and must not be flagged as
    unknown alongside node properties."""
    props: set[str] = set()
    schema = schema or {}
    for key in ("relationship_properties", "relationship_property_types"):
        block = schema.get(key) or {}
        if isinstance(block, dict):
            for plist in block.values():
                # values may be a list of prop names, or a dict {prop: type}
                if isinstance(plist, list):
                    props.update(str(p) for p in plist)
                elif isinstance(plist, dict):
                    props.update(str(p) for p in plist.keys())
    return props


def unknown_cypher_properties(cypher: str, known_props: set[str]) -> list[str]:
    """Return distinct `<var>.<Prop>` property names in the Cypher that are not
    in known_props, preserving first-seen order. Catches hallucinated attributes
    like `s.Lab` before the query runs.

    Scans the MASKED cypher. Sample type codes are dotted — D.SEQ, A.SCXP,
    D.FLOW — so an inlined literal like `WHERE s.type = 'D.SEQ'` reads to
    `_CYPHER_PROP_RE` as a property access on a variable named `D`, and the guard
    rejects a valid query with "properties ['SEQ'] do not exist". That is exactly
    what happened to repro.cypher_uid_dot in the 2026-07-29 probe run.

    It is nondeterministic in practice, which is what makes it nasty: the same
    question bound the value as `$type` in the seed-0 run and passed, then
    inlined it in the probe run and failed. Masking removes the whole class,
    and both other guards in this module already scan the mask.
    """
    out: list[str] = []
    for prop in _CYPHER_PROP_RE.findall(_mask_cypher(cypher or "")):
        if prop not in known_props and prop not in out:
            out.append(prop)
    return out


# --------------------------------------------------------------------------- #
# OPTIONAL MATCH ... WHERE guard
#
# A WHERE that immediately follows an OPTIONAL MATCH becomes part of the optional
# pattern rather than a row filter, so non-matching rows survive with nulls instead
# of being removed. Measured live on task 783's exact Cypher: 50,161 rows without an
# intervening WITH, 705 with it — 50,161 being every sample in any study, i.e. a
# result independent of both bound parameters.
#
# The prompt already carries the WITH; the model drops it roughly 40% of the time,
# which is why this is a deterministic guard rather than another prompt line.
# --------------------------------------------------------------------------- #

# Ordered longest-first so `OPTIONAL MATCH` wins over `MATCH`.
_CLAUSE_RE = re.compile(
    r"\b(OPTIONAL\s+MATCH|DETACH\s+DELETE|ORDER\s+BY|MATCH|WHERE|WITH|RETURN|UNWIND"
    r"|CALL|MERGE|CREATE|SET|DELETE|REMOVE|FOREACH|SKIP|LIMIT|UNION)\b",
    re.IGNORECASE,
)

# `(s:Sample)`, `(st)`, `[r:DERIVED_FROM]`, `(s {id: 1})` — but not `toLower(st.title)`
# (the `.` is not an accepted terminator) and not `[:IN_STUDY]` (no identifier).
_PATTERN_VAR_RE = re.compile(r"[(\[]\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?=[:)\]{])")

_AS_ALIAS_RE = re.compile(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_BINDING_CLAUSES = {"MATCH", "OPTIONAL MATCH", "MERGE", "CREATE"}

# Blanks string literals, backtick identifiers, `$params` and comments with spaces, so
# every offset still lines up with the original text: clause scanning runs on the mask,
# slicing on the original. The implementation moved to cypher_text (the Neo4j tool's
# write check masks the same way); this module keeps the name.
_mask_cypher = cypher_text.mask_cypher


# --------------------------------------------------------------------------- #
# Canonical sample-UID property
#
# The cached Neo4j schema lists BOTH `uuid` and `UID` under node_properties.Sample,
# while prompts/graph_agent.txt states "Sample nodes have exactly three properties:
# uuid, type, id" and "The canonical UID property is `uuid` (lowercase)". Handed a
# schema that advertises `UID`, the model sometimes believes the schema.
#
# Task 855 did exactly that: correctly routed to graph with both UIDs bound, then
# filtered on `WHERE nhp.UID IN $uids`, matched nothing, and reported a confident
# negative. The 2026-07-24 run asked the same question using `uuid` and returned the
# correct six records.
#
# The property guard is not involved — `UID` IS in the schema, so flagging it as
# unknown would be wrong. `UID` appears on no other label (Study/Investigation/
# SampleType carry only title/id/description/project_id), so the rewrite is
# unambiguous. Deterministic for the same reason the filter guard is: the prompt
# already says this and the model does it anyway.
#
# v1.1 does not write the metadata key UID at all (it equals uuid), so the rewrite is
# right on both schemas.
# --------------------------------------------------------------------------- #

# `nhp.UID` but not `AS UID` (no dot) and not `.UID` inside a string literal (the
# mask blanks those before this ever sees them).
_SAMPLE_UID_PROP_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.UID\b")


def canonicalize_sample_uid_property(cypher: str) -> tuple[str, list[str]]:
    """Rewrite `<var>.UID` property reads to the canonical `<var>.uuid`.

    Returns (cypher, notes); notes is empty when nothing was rewritten.
    """
    if not cypher:
        return cypher, []
    masked = _mask_cypher(cypher)
    notes: list[str] = []
    pieces: list[str] = []
    last = 0
    for m in _SAMPLE_UID_PROP_RE.finditer(masked):
        var = m.group(1)
        pieces.append(cypher[last:m.start()])
        pieces.append(f"{var}.uuid")
        last = m.end()
        notes.append(f"{var}.UID -> {var}.uuid")
    if not notes:
        return cypher, []
    pieces.append(cypher[last:])
    return "".join(pieces), notes


def _split_clauses(masked: str) -> list[tuple[str, int, int, int]]:
    """Return (KEYWORD, keyword_start, body_start, body_end) for each clause, in order."""
    matches = list(_CLAUSE_RE.finditer(masked))
    clauses = []
    for idx, m in enumerate(matches):
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(masked)
        keyword = " ".join(m.group(1).upper().split())
        clauses.append((keyword, m.start(), m.end(), body_end))
    return clauses


def _bound_vars(keyword: str, body: str) -> list[str]:
    """Variables a clause introduces, in declaration order."""
    found: list[str] = []

    def add(name):
        if name not in found:
            found.append(name)

    if keyword in _BINDING_CLAUSES:
        for name in _PATTERN_VAR_RE.findall(body):
            add(name)
    elif keyword in ("WITH", "UNWIND", "RETURN"):
        for item in body.split(","):
            alias = _AS_ALIAS_RE.search(item)
            if alias:
                add(alias.group(1))
                continue
            bare = item.strip()
            if keyword == "WITH" and _IDENT_RE.fullmatch(bare):
                add(bare)
    return found


def optional_match_filter_leaks(cypher: str | None) -> list[dict]:
    """
    Find every `OPTIONAL MATCH` whose next clause is a `WHERE` that references a
    variable bound *before* the optional pattern.

    That reference is the discriminator. A WHERE legitimately belonging to an optional
    pattern can only constrain what that pattern introduced; the moment it touches a
    previously-bound variable the author meant a row filter, and Cypher will silently
    discard it.

    Returns one dict per leak with the WHERE's offset in the original string and the
    variables the repairing `WITH` must carry.
    """
    if not cypher or not cypher.strip():
        return []

    masked = _mask_cypher(cypher)
    clauses = _split_clauses(masked)
    leaks: list[dict] = []
    prior: list[str] = []

    for idx, (keyword, kw_start, body_start, body_end) in enumerate(clauses):
        if keyword == "OPTIONAL MATCH" and idx + 1 < len(clauses):
            next_keyword, next_kw_start, next_body_start, next_body_end = clauses[idx + 1]
            if next_keyword == "WHERE":
                new_vars = [v for v in _bound_vars(keyword, masked[body_start:body_end]) if v not in prior]
                where_idents = set(_IDENT_RE.findall(masked[next_body_start:next_body_end]))
                if where_idents & set(prior):
                    leaks.append({
                        "where_offset": next_kw_start,
                        "with_vars": prior + new_vars,
                        "where_preview": " ".join(cypher[next_kw_start:next_body_end].split())[:80],
                    })
        for var in _bound_vars(keyword, masked[body_start:body_end]):
            if var not in prior:
                prior.append(var)

    return leaks


def repair_optional_match_filters(cypher: str | None) -> tuple[str | None, list[str]]:
    """
    Insert the missing `WITH` before any WHERE that Cypher would otherwise fold into
    the preceding optional pattern. Returns (cypher, notes); notes is empty when
    nothing was changed.

    Fails soft by design: any error returns the input untouched. A guard that can
    break a working query is worse than the bug it fixes. A repaired query that is
    somehow a syntax error is still caught by the existing Neo4j retry.
    """
    try:
        leaks = optional_match_filter_leaks(cypher)
        if not leaks:
            return cypher, []

        repaired = cypher
        notes: list[str] = []
        # Right-to-left so earlier offsets stay valid.
        for leak in sorted(leaks, key=lambda x: x["where_offset"], reverse=True):
            offset = leak["where_offset"]
            line_start = repaired.rfind("\n", 0, offset) + 1
            indent = repaired[line_start:offset]
            if indent.strip():
                indent = ""
            clause = "WITH " + ", ".join(leak["with_vars"])
            repaired = repaired[:offset] + f"{clause}\n{indent}" + repaired[offset:]
            notes.append(f"inserted `{clause}` before `{leak['where_preview']}`")

        for note in reversed(notes):
            print(f"[DEBUG][GRAPH][WITH_GUARD] {note}")
        return repaired, list(reversed(notes))
    except Exception as e:  # never break a working query
        print(f"[DEBUG][GRAPH][WITH_GUARD] guard failed, leaving cypher untouched: {e!r}")
        return cypher, []


# --------------------------------------------------------------------------- #
# The v1.1 catalog guard and the whole-node guard (spec section 4.3, D13)
#
# With the catalog live (graph_catalog.get_snapshot), a property read is checked
# against the labels of its variable instead of the type-blind union of the committed
# JSON: a `T_X` variable may read the Sample system properties and the attributes of
# type X that hold a value; a plain `Sample` variable the union over every type; other
# labels and relationship types their v1.1 property sets; a variable whose label is
# unknown (or that no pattern binds) everything. Labels come from node patterns
# (`(s:Sample:T_TIS)`) and label predicates (`WHERE s:T_TIS`), and pass through a bare
# alias (`WITH s AS t`). Backticked names (s.`Catalog#`), map projections
# (`s {.Organ}`) and pattern property maps (`(s:T_TIS {Organ: 'Lung'})`) are checked;
# a dotted name followed by `(` is a function or procedure (`date.truncate(`,
# `db.index.fulltext.queryNodes(`), not a property. A `T_` label that names no sample
# type is reported on its own.
#
# A whole Sample node returned or collected (`RETURN s`, `collect(s)`, `s {.*}`,
# `properties(s)`, a path over samples) ships every attribute into the chatter, the
# session and the downloads, so it is sent back for the same single repair.
#
# Both checks scan the masked text, so literals, parameters and comments never count.
# --------------------------------------------------------------------------- #

CONTEXT_CATALOG, CONTEXT_FALLBACK = "catalog", "fallback"

# docs/neo4j-schema.md "v1.1", Nodes: the system properties every Sample carries.
V11_SYSTEM_PROPERTIES = frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at"})

# docs/neo4j-schema.md "v1.2: what the sync adds". graph_sync writes three more system properties on every Sample:
# `source_hash` (the digest it re-syncs on) and the projection-owned `parent_titles` / `parent_title_hashes`. The
# guard must allow them or it refuses correct Cypher against the graph that is actually deployed, and the agent
# reads that refusal as its own query being wrong: it repairs once, then is refused again. V11_SYSTEM_PROPERTIES
# stays as the v1.1 record because a test pins it to the v1.1 section of the document.
V12_SYSTEM_PROPERTIES = V11_SYSTEM_PROPERTIES | {"source_hash", "parent_titles", "parent_title_hashes"}

# docs/neo4j-schema.md "v1.1", Relationships; DERIVED_FROM keeps its v1.0 properties.
V11_RELATIONSHIP_PROPERTIES: dict[str, frozenset[str]] = {
    "DERIVED_FROM": frozenset({"child_id", "parent_id", "assay_id", "internal_assay_id", "internal_assay_title",
                               "protocol_id", "protocol_title", "internal_assay_ids", "internal_assay_titles"}),
    "OF_TYPE": frozenset(),
    "HAS_ATTRIBUTE": frozenset(),
    "IN_PROJECT": frozenset(),
    "MEMBER_OF": frozenset({"has_left", "time_left_at"}),
    "IN_STUDY": frozenset(),
    "IN_INVESTIGATION": frozenset(),
}

# docs/neo4j-schema.md "v1.1", Nodes, for every label but Sample (whose metadata is the catalog's) and
# OrphanSample (which keeps whatever the former Sample carried, so it is checked against everything). Attribute also
# carries the statistics graph_catalog.TYPES_ADMIN reads when present (graph_search follow-up 2 writes them).
V11_NODE_PROPERTIES: dict[str, frozenset[str]] = {
    "SampleType": frozenset({"id", "title", "label", "uuid", "seek_description", "deprecated", "sample_count",
                             "attribute_count", "has_context", "name", "summary", "tags", "curated_parents",
                             "curated_children", "clade"}),
    "Attribute": frozenset({"key", "id", "sample_type_id", "sample_type", "title", "pos", "required", "is_title",
                            "base_type", "value_type", "declared", "seek_description", "meaning", "role", "unit_key",
                            "needs_backticks", "sample_count", "top_values", "top_counts", "num_min", "num_max",
                            "date_min", "date_max"}),
    "Project": frozenset({"id", "title"}),
    "Person": frozenset({"id"}),
    "Study": frozenset({"id", "title", "description", "DOI", "PMID", "seek_study_id"}),
    "Investigation": frozenset({"id", "title", "description", "project_id"}),
    # v1.2 adds label_maps_hash here; writer.GRAPHMETA_KEYS is the source of truth for this node.
    "GraphMeta": frozenset({"schema_version", "catalog_hash", "label_maps_hash", "synced_at"}),
}

_KNOWN_LABELS = frozenset({"Sample", "OrphanSample"}) | frozenset(V11_NODE_PROPERTIES) | frozenset(
    V11_RELATIONSHIP_PROPERTIES)
_ALL_V11_PROPERTIES = (V12_SYSTEM_PROPERTIES.union(*V11_NODE_PROPERTIES.values())
                       .union(*V11_RELATIONSHIP_PROPERTIES.values()))

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_NAME_RE = re.compile(_NAME)
_LABEL_EXPR = rf":\s*!?\s*{_NAME}(?:\s*[:|&]\s*!?\s*{_NAME})*"
_NODE_PATTERN_RE = re.compile(
    rf"\(\s*(?P<var>{_NAME})?\s*(?P<labels>{_LABEL_EXPR})?\s*(?P<props>\{{[^{{}}]*\}})?\s*\)")
_REL_PATTERN_RE = re.compile(
    rf"\[\s*(?P<var>{_NAME})?\s*(?P<types>:\s*!?\s*{_NAME}(?:\s*\|\s*:?\s*!?\s*{_NAME})*)?"
    rf"\s*(?P<hops>\*[\s\d.]*)?\s*(?P<props>\{{[^{{}}]*\}})?\s*\]")
_LABEL_PREDICATE_RE = re.compile(rf"(?<![\w.$])(?P<var>{_NAME})\s*(?P<labels>{_LABEL_EXPR})")
_CHAIN_RE = re.compile(rf"(?<![\w.$])(?P<var>{_NAME})\.")
_MAP_PROJECTION_RE = re.compile(rf"(?<![\w.$])(?P<var>{_NAME})\s*\{{")
_PATH_ASSIGN_RE = re.compile(
    rf"(?<![\w.$])(?P<var>{_NAME})\s*=\s*(?:(?:allShortestPaths|shortestPath)\s*\(\s*)?(?=\()", re.IGNORECASE)
_COLLECT_RE = re.compile(rf"(?<![\w.$])collect\s*\(\s*(?:DISTINCT\s+)?(?P<var>{_NAME})\s*\)", re.IGNORECASE)
_PROPERTIES_RE = re.compile(rf"(?<![\w.$])properties\s*\(\s*(?P<var>{_NAME})\s*\)", re.IGNORECASE)
_NODES_RE = re.compile(rf"(?<![\w.$])nodes\s*\(\s*(?P<var>{_NAME})\s*\)", re.IGNORECASE)
_YIELD_NODE_RE = re.compile(rf"\bYIELD\s+node\b(?:\s+AS\s+(?P<alias>{_NAME}))?", re.IGNORECASE)
_ITEM_ALIAS_RE = re.compile(rf"^(?:DISTINCT\s+)?(?P<expr>.*?)(?:\s+AS\s+(?P<alias>{_NAME}))?$",
                            re.IGNORECASE | re.DOTALL)
# A word before `{` that opens a subquery or a map literal, not a map projection.
_NOT_A_PROJECTION = frozenset({
    "EXISTS", "COUNT", "COLLECT", "CALL", "WHERE", "AND", "OR", "XOR", "NOT", "RETURN", "WITH", "IN", "THEN", "ELSE",
    "CASE", "WHEN", "DISTINCT", "YIELD", "UNWIND", "AS", "SET", "MERGE", "CREATE", "MATCH", "OPTIONAL", "UNION",
})
# Relationships whose source node is a Sample (DERIVED_FROM: both ends).
_SAMPLE_SOURCE_RELATIONSHIPS = frozenset({"IN_STUDY", "OF_TYPE"})
_WHOLE_NODE_ALTERNATIVE = (
    "return s.id, s.uuid, s.type and the named properties the question needs, and count with count(*)")


@dataclass
class _Scan:
    """What one Cypher text binds and reads, from its mask (literals, parameters and comments blanked)."""

    masked: str
    blanked: str  # the mask with node and relationship patterns blanked too
    node_labels: dict[str, dict[str, None]] = field(default_factory=dict)  # var -> labels, in first-use order
    rel_types: dict[str, dict[str, None]] = field(default_factory=dict)  # var -> relationship types
    label_uses: list[tuple[int, str]] = field(default_factory=list)
    reads: list[tuple[int, str, Any, str]] = field(default_factory=list)  # (pos, 'var'|'labels'|'types', key, prop)
    stars: list[tuple[int, str]] = field(default_factory=list)  # `v {.*}`
    aliases: list[tuple[str, str]] = field(default_factory=list)  # (source var, alias)
    samples: set[str] = field(default_factory=set)  # variables that hold a Sample node
    paths: set[str] = field(default_factory=set)  # path variables over Sample nodes
    clauses: list[tuple[str, int, int, int]] = field(default_factory=list)
    # every node and relationship pattern, sorted: (start, end, 'node'|'rel', var, labels or types)
    elements: list[tuple[int, int, str, str | None, list[str]]] = field(default_factory=list)


def _label_names(text: str | None) -> list[str]:
    return _NAME_RE.findall(text or "")


def _read_backticked(original: str, i: int) -> tuple[str | None, int]:
    """The name in the backticks opening at ``original[i]`` (a doubled backtick is a literal one), and its end."""
    out: list[str] = []
    j = i + 1
    while j < len(original):
        if original[j] == "`":
            if j + 1 < len(original) and original[j + 1] == "`":
                out.append("`")
                j += 2
                continue
            return "".join(out), j + 1
        out.append(original[j])
        j += 1
    return None, len(original)


def _skip_blank(masked: str, original: str, i: int, end: int) -> int:
    """Skip blanks in the mask, stopping on a backtick (a masked name, not space)."""
    while i < end and masked[i].isspace() and original[i] != "`":
        i += 1
    return i


def _name_at(masked: str, original: str, i: int, end: int) -> tuple[str | None, int]:
    """A plain or backticked name starting at ``i``, and where it ends."""
    if i < end and original[i] == "`":
        return _read_backticked(original, i)
    m = _NAME_RE.match(masked, i, end)
    return (m.group(0), m.end()) if m else (None, i)


def _items(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """The comma-separated items at depth 0 of ``masked[start:end]``, stopping at an unmatched closing bracket."""
    spans: list[tuple[int, int]] = []
    depth, item_start = 0, start
    for i in range(start, end):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                end = i
                break
        elif ch == "," and depth == 0:
            spans.append((item_start, i))
            item_start = i + 1
    spans.append((item_start, end))
    return spans


def _map_keys(masked: str, original: str, start: int, end: int) -> list[tuple[int, str]]:
    """The keys of a `{key: value, ...}` map whose braces enclose ``start:end``."""
    keys = []
    for s, e in _items(masked, start, end):
        i = _skip_blank(masked, original, s, e)
        name, j = _name_at(masked, original, i, e)
        if not name:
            continue
        while j < e and masked[j].isspace():
            j += 1
        if j < e and masked[j] == ":":
            keys.append((i, name))
    return keys


def _projection_items(masked: str, original: str, start: int, end: int) -> list[tuple[int, str]]:
    """The `.name` items (and `.*`, as '*') of a map projection whose braces enclose ``start:end``."""
    found = []
    for s, e in _items(masked, start, end):
        i = _skip_blank(masked, original, s, e)
        if i >= e or masked[i] != ".":
            continue
        j = _skip_blank(masked, original, i + 1, e)
        if j < e and masked[j] == "*":
            found.append((j, "*"))
            continue
        name, _ = _name_at(masked, original, j, e)
        if name:
            found.append((j, name))
    return found


def _matching_close(masked: str, i: int) -> int:
    """Index of the `}` closing the `{` at ``i``, or -1."""
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == "{":
            depth += 1
        elif masked[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _keyword_is_name(masked: str, start: int, end: int) -> bool:
    """A clause-shaped word used as a property, label or map key (`s.Set`, `:Match`, `{with: 1}`)."""
    if start > 0 and masked[start - 1] in ".:":
        return True
    return masked[end:].lstrip().startswith(":")


def _clause_spans(masked: str) -> list[tuple[str, int, int, int]]:
    """(KEYWORD, keyword_start, body_start, body_end) for each clause keyword that is not a name."""
    matches = [m for m in _CLAUSE_RE.finditer(masked) if not _keyword_is_name(masked, m.start(), m.end())]
    spans = []
    for idx, m in enumerate(matches):
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(masked)
        spans.append((" ".join(m.group(1).upper().split()), m.start(), m.end(), body_end))
    return spans


def _brace_kinds(masked: str) -> list[tuple[int, int, str]]:
    """(open, close, kind) per brace pair: 'collect' (a COLLECT subquery), 'subquery' or 'map'."""
    stack: list[tuple[int, str]] = []
    spans: list[tuple[int, int, str]] = []
    for i, ch in enumerate(masked):
        if ch == "{":
            j = i - 1
            while j >= 0 and masked[j].isspace():
                j -= 1
            word = re.search(rf"({_NAME})$", masked[max(0, j - 40):j + 1]) if j >= 0 else None
            keyword = word.group(1).upper() if word else ""
            if keyword == "COLLECT":
                kind = "collect"
            elif keyword in ("EXISTS", "COUNT", "CALL") or (j >= 0 and masked[j] == ")"):
                kind = "subquery"
            else:
                kind = "map"
            stack.append((i, kind))
        elif ch == "}" and stack:
            start, kind = stack.pop()
            spans.append((start, i, kind))
    return spans


def _scan(cypher: str) -> _Scan:
    masked = _mask_cypher(cypher)
    scan = _Scan(masked=masked, blanked=masked)
    elements: list[tuple[int, int, str, str | None, list[str]]] = []  # (start, end, 'node'|'rel', var, names)

    for m in _NODE_PATTERN_RE.finditer(masked):
        props = m.group("props")
        if props and _projection_items(masked, cypher, m.start("props") + 1, m.end("props") - 1):
            continue  # `(s {.Organ})` is a map projection inside a call, not a node pattern
        var, labels = m.group("var"), _label_names(m.group("labels"))
        scan.label_uses += [(m.start(), label) for label in labels]
        if var:
            scan.node_labels.setdefault(var, {}).update(dict.fromkeys(labels))
        if props:
            for pos, key in _map_keys(masked, cypher, m.start("props") + 1, m.end("props") - 1):
                scan.reads.append((pos, "var", var, key) if var else (pos, "labels", tuple(labels), key))
        elements.append((m.start(), m.end(), "node", var, labels))

    for m in _REL_PATTERN_RE.finditer(masked):
        k = m.start() - 1
        while k >= 0 and masked[k].isspace():
            k -= 1
        if k < 0 or masked[k] != "-":
            continue  # a list or an index, not `-[...]-`
        var, types = m.group("var"), _label_names(m.group("types"))
        if var:
            scan.rel_types.setdefault(var, {}).update(dict.fromkeys(types))
        if m.group("props"):
            for pos, key in _map_keys(masked, cypher, m.start("props") + 1, m.end("props") - 1):
                scan.reads.append((pos, "var", var, key) if var else (pos, "types", tuple(types), key))
        elements.append((m.start(), m.end(), "rel", var, types))

    blanked = list(masked)
    for start, end, *_ in elements:
        blanked[start:end] = " " * (end - start)
    scan.blanked = "".join(blanked)

    for m in _LABEL_PREDICATE_RE.finditer(scan.blanked):
        names = [n for n in _label_names(m.group("labels")) if n.startswith("T_") or n in _KNOWN_LABELS]
        if not names:
            continue  # a map key (`{title: s.title}`), not a label predicate
        var = m.group("var")
        scan.label_uses += [(m.start(), name) for name in names]
        if var in scan.rel_types and var not in scan.node_labels:
            scan.rel_types[var].update(dict.fromkeys(names))
        else:
            scan.node_labels.setdefault(var, {}).update(dict.fromkeys(names))

    if "queryNodes" in masked:  # db.index.fulltext.queryNodes: the only fulltext index is on Sample
        for m in _YIELD_NODE_RE.finditer(masked):
            scan.node_labels.setdefault(m.group("alias") or "node", {})["Sample"] = None

    for m in _CHAIN_RE.finditer(masked):
        prop, q = _name_at(masked, cypher, m.end(), len(masked))
        if not prop:
            continue
        while q < len(masked) and masked[q] == ".":  # the rest of a dotted name
            nxt = _NAME_RE.match(masked, q + 1)
            if not nxt:
                break
            q = nxt.end()
        while q < len(masked) and masked[q].isspace():
            q += 1
        if q < len(masked) and masked[q] == "(":
            continue  # a function or procedure: date.truncate(, db.index.fulltext.queryNodes(
        scan.reads.append((m.start(), "var", m.group("var"), prop))

    for m in _MAP_PROJECTION_RE.finditer(scan.blanked):
        var = m.group("var")
        if var.upper() in _NOT_A_PROJECTION:
            continue
        close = _matching_close(scan.blanked, m.end() - 1)
        if close == -1:
            continue
        for pos, name in _projection_items(scan.blanked, cypher, m.end(), close):
            if name == "*":
                scan.stars.append((pos, var))
            else:
                scan.reads.append((pos, "var", var, name))

    scan.clauses = _clause_spans(masked)
    for keyword, _, body_start, body_end in scan.clauses:
        if keyword not in ("WITH", "RETURN"):
            continue
        for s, e in _items(masked, body_start, body_end):
            item = _ITEM_ALIAS_RE.match(masked[s:e].strip())
            if item and item.group("alias") and _NAME_RE.fullmatch(item.group("expr").strip()):
                scan.aliases.append((item.group("expr").strip(), item.group("alias")))
    for source, alias in scan.aliases:
        if source in scan.node_labels:
            scan.node_labels.setdefault(alias, {}).update(scan.node_labels[source])
        if source in scan.rel_types:
            scan.rel_types.setdefault(alias, {}).update(scan.rel_types[source])

    scan.samples = {v for v, labels in scan.node_labels.items()
                    if "Sample" in labels or any(label.startswith("T_") for label in labels)}
    elements.sort()
    scan.elements = elements
    for i in range(1, len(elements) - 1):
        left, rel, right = elements[i - 1], elements[i], elements[i + 1]
        if left[2] != "node" or rel[2] != "rel" or right[2] != "node" or len(rel[4]) != 1:
            continue
        before, after = masked[left[1]:rel[0]], masked[rel[1]:right[0]]
        if not re.fullmatch(r"\s*<?\s*-\s*", before) or not re.fullmatch(r"\s*-\s*>?\s*", after):
            continue
        rtype, into_right, into_left = rel[4][0], ">" in after, "<" in before
        if rtype == "DERIVED_FROM":
            scan.samples.update(v for v in (left[3], right[3]) if v)
        elif rtype in _SAMPLE_SOURCE_RELATIONSHIPS and into_right != into_left:
            source = left[3] if into_right else right[3]
            if source:
                scan.samples.add(source)
    for source, alias in scan.aliases:
        if source in scan.samples:
            scan.samples.add(alias)

    for m in _PATH_ASSIGN_RE.finditer(masked):
        clause = next((c for c in reversed(scan.clauses) if c[1] < m.start()), None)
        if clause is None or clause[0] not in ("MATCH", "OPTIONAL MATCH"):
            continue
        span = [e for e in elements if m.end() <= e[0] < clause[3]]
        if any((e[2] == "node" and (e[3] in scan.samples or "Sample" in e[4]
                                    or any(n.startswith("T_") for n in e[4])))
               or (e[2] == "rel" and set(e[4]) & ({"DERIVED_FROM"} | _SAMPLE_SOURCE_RELATIONSHIPS))
               for e in span):
            scan.paths.add(m.group("var"))
    return scan


class _Problem(NamedTuple):
    pos: int
    text: str
    kind: str  # 'property' or 'label'
    owners: tuple[str, ...]  # the labels or relationship types whose property sets applied


def _property_problems(cypher: str, snapshot) -> list[_Problem]:
    if not cypher or not cypher.strip():
        return []
    scan = _scan(cypher)
    guard = snapshot.guard
    titles = {row.label: row.title for row in snapshot.index}
    all_attributes = frozenset().union(*guard.values()) if guard else frozenset()
    everything = _ALL_V11_PROPERTIES | all_attributes

    def node_rule(labels: list[str], fallback: str):
        type_labels = [label for label in labels if label.startswith("T_")]
        if type_labels:
            known = [label for label in type_labels if label in guard]
            if not known:
                return None  # the unknown type label is reported instead
            allowed = V12_SYSTEM_PROPERTIES.union(*(guard[label] for label in known))
            allowed = allowed.union(*(V11_NODE_PROPERTIES[label] for label in labels if label in V11_NODE_PROPERTIES))
            return "|".join(titles.get(label, label) for label in known), allowed, tuple(known)
        if "Sample" in labels:
            return "Sample", V12_SYSTEM_PROPERTIES | all_attributes, ("Sample",)
        other = [label for label in labels if label in V11_NODE_PROPERTIES]
        if other:
            return "|".join(other), frozenset().union(*(V11_NODE_PROPERTIES[label] for label in other)), tuple(other)
        return fallback, everything, ()

    def rel_rule(types: list[str], fallback: str):
        known = [t for t in types if t in V11_RELATIONSHIP_PROPERTIES]
        if known:
            return "|".join(known), frozenset().union(*(V11_RELATIONSHIP_PROPERTIES[t] for t in known)), tuple(known)
        return fallback, everything, ()

    problems: list[_Problem] = []
    for pos, label in scan.label_uses:
        if label.startswith("T_") and label not in guard:
            problems.append(_Problem(pos, ":" + label, "label", (label,)))
    for pos, kind, key, prop in scan.reads:
        if kind == "labels":
            rule = node_rule(list(key), "node")
        elif kind == "types":
            rule = rel_rule(list(key), "relationship")
        elif key in scan.rel_types and key not in scan.node_labels:
            rule = rel_rule(list(scan.rel_types[key]), key)
        elif key in scan.node_labels:
            rule = node_rule(list(scan.node_labels[key]), key)
        else:
            rule = (key, everything, ())
        if rule is not None and prop not in rule[1]:
            problems.append(_Problem(pos, f"{rule[0]}.{prop}", "property", rule[2]))

    unique: dict[str, _Problem] = {}
    for problem in sorted(problems, key=lambda p: p.pos):
        unique.setdefault(problem.text, problem)
    return list(unique.values())


def catalog_unknown_properties(cypher: str, snapshot) -> list[str]:
    """Property reads the v1.1 catalog does not allow for their variable's labels, and unknown `T_` labels.

    ``["TIS.Sequencer", "D.SEQ.Catalog#", "Sample.Nope", "DERIVED_FROM.Organ", ":T_NOPE"]``: the owner is the
    sample type code (a ``T_`` label), ``Sample``, another v1.1 label or relationship type, or the variable's name
    when its label is unknown. Each problem once, in first-seen order.
    """
    return [problem.text for problem in _property_problems(cypher, snapshot)]


def whole_node_returns(cypher: str) -> list[str]:
    """Variables whose whole Sample node (or a path over samples) the query returns or collects: ``["s", ...]``.

    ``RETURN s``, ``RETURN *``, ``collect(s)`` anywhere, ``s {.*}``, ``properties(s)`` and ``nodes(p)``; a RETURN
    inside a CALL or EXISTS subquery is not sent to the caller and does not count. A Sample variable is one labelled
    ``Sample`` or ``T_<code>``, an end of DERIVED_FROM, the source of IN_STUDY or OF_TYPE, a fulltext hit, or a bare
    alias of one of these.
    """
    if not cypher or not cypher.strip():
        return []
    scan = _scan(cypher)
    masked, shipped = scan.masked, scan.samples | scan.paths
    found: list[tuple[int, str]] = []
    for regex, pool in ((_COLLECT_RE, shipped), (_PROPERTIES_RE, scan.samples), (_NODES_RE, scan.paths)):
        found += [(m.start(), m.group("var")) for m in regex.finditer(masked) if m.group("var") in pool]
    found += [(pos, var) for pos, var in scan.stars if var in scan.samples]

    hidden = [(start, end) for start, end, kind in _brace_kinds(masked) if kind != "collect"]
    for keyword, kw_start, body_start, body_end in scan.clauses:
        if keyword != "RETURN" or any(start < kw_start < end for start, end in hidden):
            continue
        for s, e in _items(masked, body_start, body_end):
            item = _ITEM_ALIAS_RE.match(masked[s:e].strip())
            expr = item.group("expr").strip() if item else ""
            if expr == "*":
                order = sorted(scan.samples, key=lambda v: (re.search(rf"\b{re.escape(v)}\b", masked) or
                                                            re.search(r"$", masked)).start())
                found += [(s, var) for var in order]
            elif _NAME_RE.fullmatch(expr) and expr in shipped:
                found.append((s, expr))

    out: list[str] = []
    for _, var in sorted(found):
        if var not in out:
            out.append(var)
    return out


def _names(names, limit: int = 200) -> str:
    rendered = [n if _NAME_RE.fullmatch(n) else "`" + n.replace("`", "``") + "`" for n in sorted(names)]
    if len(rendered) > limit:
        rendered = rendered[:limit] + [f"and {len(rendered) - limit} more"]
    return ", ".join(rendered) if rendered else "(none)"


def _catalog_repair_message(problems: list[_Problem], whole: list[str], snapshot) -> str:
    """The one repair prompt: every problem once, and the property names valid for each label involved."""
    titles = {row.label: row.title for row in snapshot.index}
    properties = [p.text for p in problems if p.kind == "property"]
    labels = [p.text for p in problems if p.kind == "label"]
    lines = ["The previous Cypher cannot run against this graph as written:"]
    if properties:
        lines.append("- properties the schema does not list for that label or relationship: " + ", ".join(properties))
    if labels:
        lines.append("- labels that name no sample type: " + ", ".join(labels) + " (a type label is T_ plus the "
                     "code with every character outside [A-Za-z0-9_] replaced by _; see the type index)")
    if whole:
        lines.append("- " + ", ".join(f"whole node {v}" for v in whole) + ": never return or collect a whole "
                     f"Sample node; {_WHOLE_NODE_ALTERNATIVE}")
    owners = list(dict.fromkeys(owner for p in problems if p.kind == "property" for owner in p.owners))
    for owner in owners:
        if owner in snapshot.guard:
            lines.append(f"{titles.get(owner, owner)} (:{owner}) attributes with values: "
                         f"{_names(snapshot.guard[owner])}")
        elif owner == "Sample":
            lines.append("A plain :Sample variable reads the attributes of every type; start from the type label "
                         "(MATCH (s:T_<code>)) and use the attributes that type's section lists.")
        elif owner in V11_RELATIONSHIP_PROPERTIES:
            lines.append(f"{owner} properties: {_names(V11_RELATIONSHIP_PROPERTIES[owner])}")
        elif owner in V11_NODE_PROPERTIES:
            lines.append(f"{owner} properties: {_names(V11_NODE_PROPERTIES[owner])}")
    lines.append(f"Every Sample also has the system properties {_names(V12_SYSTEM_PROPERTIES)}.")
    lines.append("Regenerate the Cypher using only properties the schema lists for each label, or return an empty "
                 "cypher and say why if the question cannot be answered from the graph.")
    return "\n".join(lines)


def _catalog_refusal(problems: list[_Problem], whole: list[str]) -> str:
    parts = []
    properties = [p.text for p in problems if p.kind == "property"]
    labels = [p.text for p in problems if p.kind == "label"]
    if properties:
        parts.append(f"properties {properties} are not in the catalog for those labels")
    if labels:
        parts.append(f"labels {labels} name no sample type")
    if whole:
        parts.append("it returns whole Sample nodes (" + ", ".join(f"whole node {v}" for v in whole) + ")")
    return "Graph agent could not produce valid Cypher; " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
# The query-shape guards: P6a and P6b of the Pilot A POC review
#
# Both refuse a *shape*, not a name, and both are string-in / list-out, so they replay over
# `PilotAPOC/review/compare_export.json` with no graph, no model call and no cost. Each
# refusal is a repair prompt first — the agent gets one retry, and the message names the
# query to write instead — and a named empty plan second. Over-refusal is this guard's own
# failure mode, so every rule below is the narrowest one the run's evidence supports and
# anything the guard cannot read is left alone.
#
# P6a, the variable-length path. Two of the 56 executed queries in run full-a used one at
# all, and both wrote `*0..`: `entity.find_pbmcs_that_were_sequenced_u` ran 173.4 s and was
# killed (the run's only timeout) and `advanced.show_me_all_facs_data_for_the` returned
# 3,688 where the key says 3,728. Neo4j expands an unbounded pattern to exhaustion over
# 1,213,093 DERIVED_FROM edges. The answer key's own traversal oracles for those two
# questions bound the hops — `*1..6` and `*1..8` — which is what the refusal asks for. The
# second condition, no anchor on either end, is the narrow case a bound does not cover: a
# bounded expansion that starts from every sample in the database is still one.
#
# NOTE for the prompt half of phase 9: `prompts/graph_agent.txt` still teaches
# `[:DERIVED_FROM*1..]` and `[:DERIVED_FROM*0..]` in three places (the hop-choice bullet and
# the two worked patterns). A query that follows the prompt is therefore refused once and
# repaired once, which costs one model call per traversal. Bounding the prompt's examples at
# `*1..MAX_DERIVATION_HOPS` removes that cost; the guard stays either way, for the same
# reason the WITH guard stays — the prompt already carries that rule and the model drops it
# about 40% of the time.
#
# P6b, the fulltext call. `sample_search_text` is analysed, so an unquoted multi-token term
# is an OR over its tokens and an unscoped call returns their union: `ChIP-seq` became
# `chip OR seq` and answered "Yes, there are 138,313 matching records" to a question whose
# true answer is none, the worst single reply in the run. The rule is "unscoped AND
# multi-token", not "unscoped", because the same unscoped index on one clean word is exact
# and free: `ImmPort` returned 4,081, which is the key.
# --------------------------------------------------------------------------- #

# The derivation chain is D.SEQ -> DNA -> TIS -> PAV -> NHP and the answer key's traversal
# oracles bound it at *1..6 and *1..8. 8 is the number the refusal names.
MAX_DERIVATION_HOPS = 8

# Labels every sample carries, so a pattern wearing one of them narrows nothing.
_UNIVERSAL_LABELS = frozenset({"Sample", "OrphanSample"})

_FULLTEXT_CALL_RE = re.compile(r"\bdb\.index\.fulltext\.queryNodes\s*\(", re.IGNORECASE)
# Lucene's own conjunctions. Case-sensitive on purpose: a lowercase `and` is a term.
_LUCENE_CONJUNCTION_RE = re.compile(r"\bAND\b|&&|(?:^|\s)\+\w")
_ANALYSER_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_PARAM_RE = re.compile(rf"\$({_NAME})")
_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"")
# `s = node` and `s.uuid = node.uuid`, but not `<=`, `>=`, `<>`, `=~` or `==`.
_EQ_JOIN_RE = re.compile(
    rf"(?<![\w.$])(?P<a>{_NAME})(?:\.{_NAME})?\s*(?<![<>!=~])=(?![=~])\s*(?P<b>{_NAME})(?:\.{_NAME})?(?![\w(])")
_LEFT_OF_REL_RE = re.compile(r"\s*<?\s*-\s*")
_RIGHT_OF_REL_RE = re.compile(r"\s*-\s*>?\s*")


class _Shape(NamedTuple):
    pos: int
    kind: str  # 'unbounded_path' | 'unanchored_path' | 'unscoped_fulltext'
    text: str  # the offending fragment, verbatim from the Cypher
    detail: str  # what makes it one: the hop range, the bare ends, the tokens the index would OR


def _matching_paren(masked: str, i: int) -> int:
    """Index of the `)` closing the `(` at ``i``, or -1."""
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == "(":
            depth += 1
        elif masked[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _hop_range(hops: str) -> tuple[int, int | None] | None:
    """``(min, max)`` for a `*`, `*3`, `*1..8`, `*..8` or `*2..` quantifier; None if unreadable.

    ``max`` is None when the pattern has no upper bound. An unreadable quantifier is a syntax
    error Neo4j will reject on its own, so the guard says nothing about it.
    """
    body = hops.lstrip("*").replace(" ", "")
    try:
        if not body:
            return 1, None
        if ".." in body:
            low, _, high = body.partition("..")
            return (int(low) if low else 1), (int(high) if high else None)
        return int(body), int(body)
    except ValueError:
        return None


def _in_clause(scan: _Scan, pos: int, keyword: str) -> bool:
    return any(kw == keyword and body_start <= pos < body_end
               for kw, _, body_start, body_end in scan.clauses)


def _filtered_variables(scan: _Scan) -> set[str]:
    """Variables the query actually constrains: a predicate, an inline map, or a fulltext hit.

    A read in RETURN or WITH is a projection and filters nothing, which is why the clause the
    read sits in matters. Reads inside a pattern are its property map (`(s:T_TIS {Organ: $o})`).
    """
    patterns = [(start, end) for start, end, kind, *_ in scan.elements if kind == "node"]
    filtered = {key for pos, kind, key, _prop in scan.reads
                if kind == "var" and key
                and (_in_clause(scan, pos, "WHERE") or any(s <= pos < e for s, e in patterns))}
    for m in _YIELD_NODE_RE.finditer(scan.masked):
        filtered.add(m.group("alias") or "node")  # the index already narrowed this one
    for source, alias in scan.aliases:
        if source in filtered:
            filtered.add(alias)
    return filtered


def _path_end(scan: _Scan, rel_start: int, rel_end: int, side: str):
    """The node pattern on one side of a relationship pattern: ``(start, end, var, labels)``."""
    found = None
    for start, end, kind, var, names in scan.elements:
        if kind != "node":
            continue
        if side == "left" and end <= rel_start and _LEFT_OF_REL_RE.fullmatch(scan.masked[end:rel_start]):
            found = (start, end, var, names)
        if side == "right" and start >= rel_end and _RIGHT_OF_REL_RE.fullmatch(scan.masked[rel_end:start]):
            return start, end, var, names
    return found


def _is_anchored(scan: _Scan, end, filtered: set[str], rel_start: int) -> bool:
    """Whether one end of a variable-length path narrows the expansion.

    Anchored by a label that is not `Sample`, by an inline property map, by a predicate
    anywhere in the query, by being a fulltext hit, or by an earlier pattern having bound it.
    """
    if end is None:
        return False
    start, stop, var, labels = end
    if any(label not in _UNIVERSAL_LABELS for label in labels):
        return True
    if "{" in scan.masked[start:stop]:
        return True
    if not var:
        return False
    if var in filtered:
        return True
    return any(kind == "node" and v == var and (s, e) != (start, stop) and s < rel_start
               for s, e, kind, v, _ in scan.elements)


def _variable_length_problems(cypher: str, scan: _Scan) -> list[_Shape]:
    """P6a: every variable-length relationship pattern with no upper bound or no anchored end."""
    problems: list[_Shape] = []
    filtered = _filtered_variables(scan)
    for m in _REL_PATTERN_RE.finditer(scan.masked):
        if not m.group("hops"):
            continue
        k = m.start() - 1
        while k >= 0 and scan.masked[k].isspace():
            k -= 1
        if k < 0 or scan.masked[k] != "-":
            continue  # a list or an index, not `-[...]-`
        bounds = _hop_range(m.group("hops"))
        if bounds is None:
            continue
        fragment = " ".join(cypher[m.start():m.end()].split())
        low, high = bounds
        if high is None:
            problems.append(_Shape(m.start(), "unbounded_path", fragment,
                                   f"no maximum hop count (the quantifier only says {low} or more)"))
        left = _path_end(scan, m.start(), m.end(), "left")
        right = _path_end(scan, m.start(), m.end(), "right")
        if not _is_anchored(scan, left, filtered, m.start()) and \
                not _is_anchored(scan, right, filtered, m.start()):
            ends = " and ".join(" ".join(cypher[e[0]:e[1]].split()) if e else "(an unnamed end)"
                               for e in (left, right))
            problems.append(_Shape(m.start(), "unanchored_path", fragment,
                                   f"neither end is anchored: {ends}"))
    return problems


def _fulltext_term(cypher: str, start: int, end: int, parameters) -> str | None:
    """The text the fulltext index would search, or None when the guard cannot read it.

    One `$param` and no literal resolves through ``parameters``; one literal and no `$param`
    resolves to itself. Anything else — a concatenation, several parameters, an unbound name —
    is unresolved, and an unresolved term is never a refusal.

    Both are read off the original Cypher, not the mask, which blanks exactly these two.
    """
    argument = cypher[start:end]
    params = _PARAM_RE.findall(argument)
    literals = _LITERAL_RE.findall(argument)
    if len(params) == 1 and not literals:
        value = (parameters or {}).get(params[0])
        return value if isinstance(value, str) else None
    if len(literals) == 1 and not params:
        return literals[0][0] or literals[0][1]
    return None


def _analyser_tokens(term: str) -> list[str]:
    """The tokens the index's analyser would produce: it splits on everything but letters and
    digits and lowercases, which is why `ChIP-seq` matches every `seq` in the database."""
    return [t.lower() for t in _ANALYSER_TOKEN_RE.findall(term)]


def _is_one_lucene_unit(term: str) -> bool:
    """A term the index will not silently turn into an OR: one token, a quoted phrase, or an
    explicit conjunction (`chip AND seq`, `+chip +seq`)."""
    text = term.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        return True
    if _LUCENE_CONJUNCTION_RE.search(text):
        return True
    return len(_analyser_tokens(text)) < 2


def _joined_to(scan: _Scan, hit: str) -> set[str]:
    """``hit`` and every variable the query ties to it: `s = node`, `(s:T_X {uuid: node.uuid})`,
    `WITH node AS s`."""
    joined = {hit}
    for _ in range(4):  # transitive, and four hops is more than any real query needs
        before = len(joined)
        for m in _EQ_JOIN_RE.finditer(scan.masked):
            a, b = m.group("a"), m.group("b")
            if a in joined:
                joined.add(b)
            if b in joined:
                joined.add(a)
        for start, end, kind, var, _labels in scan.elements:
            if kind == "node" and var and var not in joined and any(
                    read in joined for read in re.findall(rf"(?<![\w.$])({_NAME})\.", scan.masked[start:end])):
                joined.add(var)
        for source, alias in scan.aliases:
            if source in joined:
                joined.add(alias)
            if alias in joined:
                joined.add(source)
        if len(joined) == before:
            break
    return joined


def _fulltext_scope(scan: _Scan, joined: set[str]) -> str | None:
    """How the query narrows the index's hits — a type label or a predicate — or None."""
    for var in sorted(joined):
        for label in scan.node_labels.get(var, {}):
            if label not in _UNIVERSAL_LABELS:
                return f"{var}:{label}"
    for pos, kind, key, prop in scan.reads:
        if kind == "var" and key in joined and _in_clause(scan, pos, "WHERE"):
            return f"{key}.{prop}"
    return None


def _fulltext_problems(cypher: str, scan: _Scan, parameters) -> list[_Shape]:
    """P6b: every `db.index.fulltext.queryNodes` call whose hits are unscoped and whose term
    the analyser would split into an OR over several tokens."""
    problems: list[_Shape] = []
    for m in _FULLTEXT_CALL_RE.finditer(scan.masked):
        close = _matching_paren(scan.masked, m.end() - 1)
        if close == -1:
            continue
        arguments = _items(scan.masked, m.end(), close)
        if len(arguments) < 2:
            continue
        term = _fulltext_term(cypher, *arguments[1], parameters)
        if term is None or _is_one_lucene_unit(term):
            continue
        hit = _YIELD_NODE_RE.search(scan.masked, close)
        if hit is None:
            continue  # the call's nodes are never yielded, so there is nothing to scope
        joined = _joined_to(scan, hit.group("alias") or "node")
        if _fulltext_scope(scan, joined) is not None:
            continue
        tokens = _analyser_tokens(term)
        problems.append(_Shape(
            m.start(), "unscoped_fulltext",
            " ".join(cypher[m.start():close + 1].split()),
            f"the term {term!r} analyses to {', '.join(tokens)}, and nothing scopes the hits"))
    return problems


def query_shape_problems(cypher: str | None, parameters=None) -> list[_Shape]:
    """Shapes this graph must not be asked to run, in the order they appear in the Cypher.

    ``parameters`` is the plan's parameter map, needed to read a `$param` search term. Fails
    soft: a guard that cannot parse a query says nothing about it, because the alternative is
    refusing a query that works.
    """
    if not cypher or not cypher.strip():
        return []
    try:
        scan = _scan(cypher)
        problems = _variable_length_problems(cypher, scan) + _fulltext_problems(cypher, scan, parameters)
    except Exception as e:  # noqa: BLE001 (never refuse because the guard itself broke)
        print(f"[DEBUG][GRAPH][SHAPE_GUARD] guard failed, allowing the query: {e!r}")
        return []
    return sorted(problems)


def refused_query_shapes(cypher: str | None, parameters=None) -> list[str]:
    """One line per refused shape: ``["unbounded path [:DERIVED_FROM*0..]: ...", ...]``."""
    return [f"{problem.kind.replace('_', ' ')} {problem.text}: {problem.detail}"
            for problem in query_shape_problems(cypher, parameters)]


def _shape_repair_message(problems: list[_Shape]) -> str:
    """The one repair prompt: what each shape costs, and the query to write instead."""
    lines = ["The previous Cypher is well formed but its shape cannot be run against this graph:"]
    for problem in problems:
        lines.append(f"- {problem.text} — {problem.detail}")
    if any(p.kind == "unbounded_path" for p in problems):
        lines.append(
            f"Give every variable-length relationship an explicit maximum: write "
            f"`[:DERIVED_FROM*1..{MAX_DERIVATION_HOPS}]` (or `*0..{MAX_DERIVATION_HOPS}` when the zero-hop case "
            f"counts) instead of `*`, `*1..` or `*0..`. The house derivation chain D.SEQ -> DNA -> TIS -> PAV -> NHP "
            f"is four hops, the answer key's own traversal queries bound theirs at *1..6 and *1..8, and an unbounded "
            "pattern was measured on this database at 173 s before it was killed without returning anything.")
    if any(p.kind == "unanchored_path" for p in problems):
        lines.append(
            "Anchor at least one end of the path before the hops: a sample type label (`(d:T_D_SEQ)`), a property "
            "predicate on that end, or a variable an earlier clause already bound. A plain `(:Sample)` on both ends "
            "expands from all 1,084,754 samples.")
    if any(p.kind == "unscoped_fulltext" for p in problems):
        lines.append(
            "The fulltext index is analysed: it lowercases and splits on punctuation and spaces, then returns every "
            "sample matching ANY token, so an unscoped multi-word term answers with the union. Measured on this "
            "database, 'ChIP-seq' became chip OR seq and returned 138,313 of 1,084,754 samples for a question whose "
            "answer is none. Do one of these instead:\n"
            "  - match the text directly, which keeps adjacency and punctuation:\n"
            "    WHERE toLower(s.search_text) CONTAINS toLower($term)  (one OR branch per spelling)\n"
            "  - or keep the index and scope its hits to the sample type the question is about:\n"
            "    CALL db.index.fulltext.queryNodes('sample_search_text', $term) YIELD node WHERE node:T_<code>\n"
            "  - use the index unscoped only for a single clean word whose case variants should merge.")
    lines.append("Regenerate the Cypher with a runnable shape, or return an empty cypher and say why if the question "
                 "cannot be answered from the graph.")
    return "\n".join(lines)


def _shape_refusal(problems: list[_Shape]) -> str:
    """The user-facing reason, when the one repair kept the shape."""
    parts = []
    for problem in problems:
        if problem.kind == "unbounded_path":
            parts.append(f"the path {problem.text} has no maximum hop count, which does not return on this graph "
                         f"(bound it at *1..{MAX_DERIVATION_HOPS})")
        elif problem.kind == "unanchored_path":
            parts.append(f"the path {problem.text} expands from every sample in the database "
                         "(anchor one end to a sample type or a predicate)")
        else:
            parts.append(f"the unscoped fulltext call {problem.text} would return the union of its tokens rather "
                         "than an answer (match toLower(search_text) CONTAINS the phrase, or scope the hits to a "
                         "sample type)")
    return "Graph agent could not produce valid Cypher; " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
# The schema context
# --------------------------------------------------------------------------- #


class CatalogContext(NamedTuple):
    """The live catalog for one question: the snapshot the guard checks, and the two texts the agents read."""

    snapshot: Any
    schema: str  # structure, type index and the resolved types (graph_context.render_graph_context)
    vocabulary: str  # the question's vocabulary blocks, "" when none applies


def _plain(value) -> dict:
    if value is None:
        return {}
    return value.model_dump() if hasattr(value, "model_dump") else dict(value)


def live_catalog_context(config: ChatConfig, user_query: str, entity_result, parser_plan) -> CatalogContext | None:
    """The rendered v1.1 catalog for this question, or None when the committed JSON must be used (spec D7).

    The resolved types are the parser plan's first, then the entity output's (``resolved_type_codes``). Any catalog
    failure, ``CatalogUnavailable`` or not, means the fallback: the committed schema always works.
    """
    try:
        entity_dict, plan_dict = _plain(entity_result), _plain(parser_plan)
        snapshot = graph_catalog.get_snapshot(config)
        codes = graph_context.resolved_type_codes(plan_dict, entity_dict, {row.title for row in snapshot.index})
        details = graph_catalog.get_type_details(config, codes) if codes else []
        schema = graph_context.render_graph_context(snapshot, details)
        vocabulary = graph_context.render_vocabulary(graph_catalog.get_vocabulary(config), user_query or "")
    except graph_catalog.CatalogUnavailable as exc:
        print(f"[DEBUG][GRAPH] Catalog unavailable, using the committed schema: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 (a catalog defect must cost the context, not the turn)
        print(f"[DEBUG][GRAPH] Catalog context failed ({type(exc).__name__}: {exc}); using the committed schema")
        return None
    print(f"[DEBUG][GRAPH] Catalog context: {len(schema.encode('utf-8'))} bytes, types {codes}, "
          f"catalog_hash {str(snapshot.catalog_hash)[:12]}")
    return CatalogContext(snapshot, schema, vocabulary)


def graph_schema_snapshot(config: ChatConfig, *, types=(), question: str = "") -> dict:
    """The deployed graph's schema as text, read live, with the committed file as a named fallback.

    The read-only projection the ``graph-schema`` op returns, so a caller with no in-process access to
    ``graph_catalog`` (the Container-CC agent) describes the graph that is deployed rather than a snapshot
    baked into its image. It spends no model call: the catalog reads and the renderers, nothing else.

    ``types`` names sample type codes to render in full (their attributes, value types and most frequent
    values); ``question`` gates the vocabulary blocks exactly as a graph turn does. A code the catalog does
    not know is returned in ``unknown_types`` rather than guessed at.

    ``source`` is ``catalog`` when the answer is the live graph and ``fallback`` when it is the committed
    ``context/neo4j_schema.json``, and a fallback carries both why (``unavailable_reason``) and how stale the
    file is (``fallback_fetched_at``). A caller that ignores ``source`` is describing a graph that may not
    exist, which is the failure this op was added to end.
    """
    requested = list(dict.fromkeys(str(t).strip() for t in (types or ()) if str(t).strip()))
    try:
        snapshot = graph_catalog.get_snapshot(config)
        known = {row.title for row in snapshot.index}
        wanted = [code for code in requested if code in known]
        details = graph_catalog.get_type_details(config, wanted) if wanted else []
        schema = graph_context.render_graph_context(snapshot, details)
        vocabulary = graph_context.render_vocabulary(graph_catalog.get_vocabulary(config), question or "")
    except graph_catalog.CatalogUnavailable as exc:
        return _fallback_schema_snapshot(config, question, requested, str(exc))
    except Exception as exc:  # noqa: BLE001 (a catalog defect must cost the answer's freshness, not the call)
        return _fallback_schema_snapshot(config, question, requested,
                                         f"graph catalog context failed: {type(exc).__name__}: {exc}")
    return {
        "source": CONTEXT_CATALOG,
        "schema_version": snapshot.schema_version,
        "catalog_hash": snapshot.catalog_hash,
        "synced_at": snapshot.synced_at,
        "sample_types": len(snapshot.index),
        "resolved_types": [str(detail.title) for detail in details],
        "unknown_types": [code for code in requested if code not in known],
        "schema": schema,
        "vocabulary": vocabulary,
        "unavailable_reason": None,
        "fallback_fetched_at": None,
    }


def _fallback_schema_snapshot(config: ChatConfig, question: str, requested: list[str], reason: str) -> dict:
    """The committed ``neo4j_schema.json`` as the answer, saying so and saying why."""
    committed = getattr(config, "NEO4J_SCHEMA", None) or {}
    print(f"[DEBUG][GRAPH] graph-schema falling back to the committed schema: {reason}")
    return {
        "source": CONTEXT_FALLBACK,
        "schema_version": None,
        "catalog_hash": None,
        "synced_at": None,
        "sample_types": 0,
        "resolved_types": [],
        "unknown_types": list(requested),
        "schema": json.dumps(committed, indent=2) if committed else "{}",
        "vocabulary": "\n\n".join(_fallback_vocabulary(config, question or "")),
        "unavailable_reason": reason,
        "fallback_fetched_at": committed.get("fetched_at") if isinstance(committed, dict) else None,
    }


def _fallback_vocabulary(config: ChatConfig, user_query: str) -> list[str]:
    """The committed protocol and assay-connection blocks, keyword-gated as before the catalog."""
    blocks = []
    query = user_query.lower()
    if any(kw in query for kw in graph_context.PROTOCOL_WORDS) and getattr(config, "PROTOCOL_SCHEMA", None):
        protocol_titles = config.PROTOCOL_SCHEMA.get("protocol_titles", [])
        if protocol_titles:
            blocks.append("PROTOCOL VOCABULARY (DERIVED_FROM.protocol_title values):\n"
                          + json.dumps(protocol_titles, indent=2))
            print(f"[DEBUG][GRAPH] Including protocol vocabulary ({len(protocol_titles)} titles)")
    if any(kw in query for kw in graph_context.ASSAY_WORDS) and getattr(config, "ASSAY_SAMPLE_CONNECTIONS", None):
        connections = config.ASSAY_SAMPLE_CONNECTIONS.get("connections", [])
        if connections:
            blocks.append("ASSAY-SAMPLE CONNECTIONS (assay → parent_type → child_type, use to determine which side "
                          "a sample type sits on for a given assay):\n" + json.dumps(connections, indent=2))
            print(f"[DEBUG][GRAPH] Including assay-sample connections ({len(connections)} entries)")
    return blocks


def graph_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: ParserPlan | dict | None = None,
    retry_context: str | None = None,
    refine_context: str | None = None,
) -> GraphAgentPlan:
    """
    Generate a Cypher query for the given user query using the graph schema.
    Receives resolved entities from the Entity Agent and optional parser filters,
    then calls the LLM to produce a GraphAgentPlan (cypher + explanation + parameters).
    Falls back to an empty plan on structured-output failure.

    The schema is the live v1.1 catalog rendered as text when it is available (with the
    per-label property guard and the whole-node guard), else the committed JSON schema
    with the type-blind guard (spec D7). Every returned plan records which in
    ``context_mode`` (spec D15).
    """
    print("\n[DEBUG][GRAPH] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})

    catalog = live_catalog_context(config, user_query, entity_dict, plan_dict)
    context_mode = CONTEXT_CATALOG if catalog is not None else CONTEXT_FALLBACK
    if catalog is not None:
        schema_message = ("GRAPH SCHEMA (v1.1 structure, sample type index and the resolved sample types; this is "
                          "the schema):\n" + catalog.schema)
        vocabulary_messages = (["GRAPH VOCABULARY (values stored in the graph; match names against these):\n"
                                + catalog.vocabulary] if catalog.vocabulary else [])
    else:
        schema_json = json.dumps(config.NEO4J_SCHEMA, indent=2) if config.NEO4J_SCHEMA else "{}"
        schema_message = "GRAPH SCHEMA (node labels, relationships, properties, vocabulary):\n" + schema_json
        vocabulary_messages = _fallback_vocabulary(config, user_query)

    # Use full parser plan when available (contains resolved entities + routing intent + filters)
    # Fall back to raw entity dict when called without a parser plan
    if plan_dict:
        upstream_context = "PARSER PLAN (from Parser Agent — routing intent + resolved entities + filters):\n" + json.dumps(plan_dict, indent=2)
    else:
        entity_json = json.dumps(entity_dict, indent=2)
        upstream_context = "RESOLVED ENTITIES (from Entity Agent — no parser plan available):\n" + entity_json

    messages = [
        {"role": "system", "content": config.GRAPH_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": schema_message},
        {"role": "system", "content": upstream_context},
    ]
    for block in vocabulary_messages:
        messages.append({"role": "system", "content": block})
    if retry_context:
        messages.append({"role": "system", "content": retry_context})
    if refine_context:
        messages.append({"role": "system", "content": refine_context})
    messages.append({"role": "user", "content": user_query})

    graph_client, graph_model, graph_budget = config.get_agent_model("graph")

    def call(prompt: str, log_label: str) -> GraphAgentPlan:
        return call_llm_structured(
            config=config,
            prompt=prompt,
            model=GraphAgentPlan,
            system=config.GRAPH_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=graph_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label=log_label,
            thinking_budget=graph_budget,
            client=graph_client,
        )

    try:
        result = call(user_query, "graph_agent")
        print(f"[DEBUG][GRAPH] Generated cypher: {result.cypher!r}")

        # Canonical-UID guard: runs FIRST so everything downstream, including the
        # property guard and the filter guard, sees the canonical spelling.
        result.cypher, uid_notes = canonicalize_sample_uid_property(result.cypher)
        if uid_notes:
            result.explanation = (
                f"{result.explanation} [uid guard: {'; '.join(uid_notes)}]"
            ).strip()
            print(f"[DEBUG][GRAPH] Canonicalised UID property: {result.cypher!r}")

        if catalog is not None:
            # Catalog guard (spec 4.3): names checked per label against the live catalog, and no whole Sample
            # node returned (D13). One repair naming each problem; then the empty plan naming what is left.
            problems = _property_problems(result.cypher, catalog.snapshot)
            whole = whole_node_returns(result.cypher)
            if problems or whole:
                print(f"[DEBUG][GRAPH] Catalog guard: {[p.text for p in problems]} whole nodes {whole}; "
                      "attempting repair")
                messages.append({"role": "system",
                                 "content": _catalog_repair_message(problems, whole, catalog.snapshot)})
                result = call("Regenerate the Cypher.", "graph_agent_repair")
                result.cypher, _ = canonicalize_sample_uid_property(result.cypher)
                print(f"[DEBUG][GRAPH] Repaired cypher: {result.cypher!r}")
                problems = _property_problems(result.cypher, catalog.snapshot)
                whole = whole_node_returns(result.cypher)
                if problems or whole:
                    print(f"[DEBUG][GRAPH] Repair still fails the catalog guard: {[p.text for p in problems]} "
                          f"whole nodes {whole}; returning empty plan")
                    return GraphAgentPlan(cypher="", explanation=_catalog_refusal(problems, whole), parameters={},
                                          context_mode=context_mode)
        else:
            # Schema guard: reject Cypher that filters on properties no node actually has
            # (e.g. a hallucinated `s.Lab`). Re-prompt once with the error + valid props;
            # if the repair still references unknown properties, return a graceful empty plan
            # rather than running a query that can only match nothing.
            known = known_node_properties(config.NEO4J_SCHEMA) | known_relationship_properties(config.NEO4J_SCHEMA)
            unknown = unknown_cypher_properties(result.cypher, known)
            if unknown:
                print(f"[DEBUG][GRAPH] Unknown properties in cypher: {unknown}; attempting repair")
                repair = (
                    f"The previous Cypher referenced properties that do not exist on any node or relationship: "
                    f"{unknown}. Valid properties are: {sorted(known)}. "
                    "Regenerate the Cypher using ONLY existing properties, or return an empty "
                    "cypher if the question cannot be answered from the graph."
                )
                messages.append({"role": "system", "content": repair})
                result = call("Regenerate the Cypher.", "graph_agent_repair")
                print(f"[DEBUG][GRAPH] Repaired cypher: {result.cypher!r}")
                still = unknown_cypher_properties(result.cypher, known)
                if still:
                    print(f"[DEBUG][GRAPH] Repair still references unknown properties: {still}; returning empty plan")
                    return GraphAgentPlan(
                        cypher="",
                        explanation=f"Graph agent could not produce valid Cypher; properties "
                                    f"{still} do not exist on any node in the schema.",
                        parameters={},
                        context_mode=context_mode,
                    )

        # Shape guard (POC P6a, P6b): an unbounded or unanchored variable-length path, and an
        # unscoped fulltext call whose term the analyser would split into an OR. Runs after the
        # property guards, so it sees whatever Cypher they settled on, and takes the same one
        # repair then empty plan. The repair message names the query to write instead.
        shapes = query_shape_problems(result.cypher, result.parameters)
        if shapes:
            print(f"[DEBUG][GRAPH] Shape guard: {refused_query_shapes(result.cypher, result.parameters)}; "
                  "attempting repair")
            messages.append({"role": "system", "content": _shape_repair_message(shapes)})
            result = call("Regenerate the Cypher.", "graph_agent_shape_repair")
            result.cypher, _ = canonicalize_sample_uid_property(result.cypher)
            print(f"[DEBUG][GRAPH] Reshaped cypher: {result.cypher!r}")
            shapes = query_shape_problems(result.cypher, result.parameters)
            if shapes:
                print(f"[DEBUG][GRAPH] Repair keeps the refused shape: "
                      f"{[s.kind for s in shapes]}; returning empty plan")
                return GraphAgentPlan(cypher="", explanation=_shape_refusal(shapes), parameters={},
                                      context_mode=context_mode)
            result.explanation = f"{result.explanation} [shape guard: one repair]".strip()

        # Filter guard: an OPTIONAL MATCH immediately followed by WHERE folds the
        # predicate into the optional pattern, so the filter is silently discarded and
        # the query answers a different question than the one asked. Runs last so it
        # also covers cypher produced by the property repair above.
        guarded, guard_notes = repair_optional_match_filters(result.cypher)
        if guard_notes:
            result.cypher = guarded
            result.explanation = (
                f"{result.explanation} "
                f"[filter guard: {'; '.join(guard_notes)}]"
            ).strip()
            print(f"[DEBUG][GRAPH] Guarded cypher: {result.cypher!r}")
        result.context_mode = context_mode
        return result
    except Exception as e:
        print(f"[DEBUG][GRAPH] graph_agent failed: {e!r}")
        return GraphAgentPlan(cypher="", explanation=f"Graph agent error: {e}", parameters={},
                              context_mode=context_mode)

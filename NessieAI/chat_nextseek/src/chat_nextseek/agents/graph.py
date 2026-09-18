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
    # Filled only when the turn's variant allows procedures (_scan_procedure_yields):
    proc_paths: set[str] = field(default_factory=set)  # a procedure's YIELD path (apoc.path.spanningTree, ...)
    node_lists: set[str] = field(default_factory=set)  # a procedure's YIELD nodes (apoc.path.subgraphAll)


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


def _scan(cypher: str, procedures: frozenset[str] = frozenset()) -> _Scan:
    """What `cypher` binds and reads. `procedures` (a variant's allowed procedures) adds _scan_procedure_yields."""
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
    if procedures:
        _scan_procedure_yields(scan, cypher)
    return scan


# --------------------------------------------------------------------------- #
# Procedures a prompt variant allows (v2_apoc): what their YIELD binds
# --------------------------------------------------------------------------- #
#
# Only for a turn whose variant allows procedures (cypher_text.variant_procedures), so the default path is
# unchanged. A procedure's YIELD field says what it binds: `node` a Sample node (apoc.path.subgraphNodes), `nodes`
# a list of them (apoc.path.subgraphAll), `path` a path over samples (apoc.path.spanningTree, expandConfig, expand).
# The fulltext procedure keeps its own rule in _scan. An UNWIND of a node list, or of nodes(path), binds a Sample.

_PROC_CALL_RE = re.compile(rf"\bCALL\s+(?P<name>{_NAME}(?:\.{_NAME})+)\s*\(", re.IGNORECASE)
_YIELD_END_RE = re.compile(r"\b(?:WHERE|RETURN|WITH|MATCH|OPTIONAL|CALL|UNWIND|ORDER|SKIP|LIMIT|UNION|FOREACH)\b"
                           r"|[{}]", re.IGNORECASE)
_UNWIND_RE = re.compile(rf"\bUNWIND\s+(?:(?P<list>{_NAME})|nodes\s*\(\s*(?P<path>{_NAME})\s*\))\s+AS\s+"
                        rf"(?P<alias>{_NAME})\b", re.IGNORECASE)
_YIELD_ITEM_RE = re.compile(rf"^(?P<field>{_NAME}|\*)(?:\s+AS\s+(?P<alias>{_NAME}))?$", re.IGNORECASE)
_NODE_FIELDS, _NODE_LIST_FIELDS, _PATH_FIELDS = frozenset({"node"}), frozenset({"nodes"}), frozenset({"path", "paths"})


def _scan_procedure_yields(scan: _Scan, cypher: str) -> None:
    masked = scan.masked
    for m in _PROC_CALL_RE.finditer(masked):
        if m.group("name") == "db.index.fulltext.queryNodes":
            continue
        close = cypher_text._matching_paren(masked, m.end() - 1)
        if close == -1:
            continue
        rest = masked[close + 1:]
        yielded = re.match(r"\s*YIELD\b", rest, re.IGNORECASE)
        if not yielded:
            continue
        start = close + 1 + yielded.end()
        stop = _YIELD_END_RE.search(masked, start)
        for s, e in _items(masked, start, stop.start() if stop else len(masked)):
            item = _YIELD_ITEM_RE.match(masked[s:e].strip())
            if not item:
                continue
            fields = ((_NODE_FIELDS | _NODE_LIST_FIELDS | _PATH_FIELDS) if item.group("field") == "*"
                      else {item.group("field").lower()})
            for fld in fields:
                alias = item.group("alias") or fld
                if fld in _NODE_FIELDS:
                    scan.node_labels.setdefault(alias, {})["Sample"] = None
                    scan.samples.add(alias)
                elif fld in _NODE_LIST_FIELDS:
                    scan.node_lists.add(alias)
                elif fld in _PATH_FIELDS:
                    scan.proc_paths.add(alias)
    for m in _UNWIND_RE.finditer(masked):
        if (m.group("list") or "") in scan.node_lists or (m.group("path") or "") in scan.proc_paths:
            scan.samples.add(m.group("alias"))
    for _ in range(2):  # WITH x AS y, in the order written; twice covers a chain written out of order
        for source, alias in scan.aliases:
            for pool in (scan.samples, scan.proc_paths, scan.node_lists):
                if source in pool:
                    pool.add(alias)


# APOC functions that hand back a whole node or all of its properties. The first group is refused when applied to a
# Sample variable (as properties(s) is); the second builds node or path structures and is refused wherever it is used.
_APOC_NODE_ARG_RE = re.compile(
    rf"(?<![\w$])apoc\.(?:any\.properties|convert\.(?:toJson|toSortedJsonMap|toMap)"
    rf"|agg\.(?:first|last|nth|slice|maxItems|minItems))\s*\(\s*(?:DISTINCT\s+)?(?P<var>{_NAME})\s*[,)]",
    re.IGNORECASE)
_APOC_NODE_BUILDER_RE = re.compile(
    r"(?<![\w$])(?P<fn>apoc\.(?:map\.fromNodes|agg\.graph|coll\.sortNodes|convert\.toNode|convert\.toNodeList"
    r"|path\.create|path\.combine|path\.slice|path\.elements|nodes\.get|get\.nodes))\s*\(", re.IGNORECASE)
_NODES_OF_RE = re.compile(rf"(?<![\w.$])nodes\s*\(\s*(?P<var>{_NAME})\s*\)", re.IGNORECASE)
_PREDICATE_FUNCTIONS = frozenset({"ANY", "ALL", "NONE", "SINGLE", "REDUCE"})


def _enclosing_open(masked: str, pos: int) -> int:
    """Index of the innermost `(` or `[` still open at `pos`, or -1."""
    depth = 0
    for j in range(pos - 1, -1, -1):
        ch = masked[j]
        if ch in ")]}":
            depth += 1
        elif ch in "([{":
            if depth == 0:
                return j if ch in "([" else -1
            depth -= 1
    return -1


def _nodes_of_path_is_named(masked: str, start: int, end: int, var: str) -> bool:
    """Whether `nodes(var)` at `masked[start:end]` is used for names or counts rather than returned as nodes.

    Allowed: `size(nodes(p))`, `last(nodes(p)).uuid` / `head(...)`, `nodes(p)[i].uuid`, a comprehension that projects
    (`[n IN nodes(p) | n.uuid]`), a predicate or reduce over it (`any(n IN nodes(p) WHERE ...)`), and an UNWIND (whose
    alias the scan then tracks as a Sample).
    """
    before = masked[:start].rstrip()
    after = masked[end:]
    if re.search(r"\bsize\s*\($", before, re.IGNORECASE):
        return True
    if re.search(r"\b(?:last|head)\s*\($", before, re.IGNORECASE) and re.match(rf"\s*\)\s*\.\s*{_NAME}", after):
        return True
    if re.match(rf"\s*\[[^\]]*\]\s*\.\s*{_NAME}", after):
        return True
    if re.search(r"\bUNWIND$", before, re.IGNORECASE):
        return True
    loop = re.search(rf"({_NAME})\s+IN$", before, re.IGNORECASE)
    if not loop:
        return False
    opener = _enclosing_open(masked, start)
    if opener == -1:
        return False
    if masked[opener] == "(":
        word = re.search(rf"({_NAME})\s*$", masked[:opener])
        return bool(word) and word.group(1).upper() in _PREDICATE_FUNCTIONS
    closer = _matching_close_bracket(masked, opener)
    if closer == -1:
        return False
    tail = masked[end:closer]
    bar = next((i for i, ch in enumerate(tail) if ch == "|" and _depth(tail, i) == 0), -1)
    if bar == -1:
        return False  # `[n IN nodes(p) WHERE ...]` keeps the nodes
    projection = tail[bar + 1:].strip()
    return bool(projection) and projection != loop.group(1)


def _matching_close_bracket(masked: str, i: int) -> int:
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == "[":
            depth += 1
        elif masked[j] == "]":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _depth(text: str, i: int) -> int:
    depth = 0
    for ch in text[:i]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth


def _procedure_whole_nodes(scan: _Scan) -> list[tuple[int, str]]:
    """The whole-node uses only a turn whose variant allows procedures can write (see whole_node_returns)."""
    masked = scan.masked
    found: list[tuple[int, str]] = []
    for m in _NODES_OF_RE.finditer(masked):
        if m.group("var") in scan.proc_paths and not _nodes_of_path_is_named(masked, m.start(), m.end(),
                                                                               m.group("var")):
            found.append((m.start(), m.group("var")))
    pool = scan.samples | scan.proc_paths | scan.node_lists
    found += [(m.start(), m.group("var")) for m in _APOC_NODE_ARG_RE.finditer(masked) if m.group("var") in pool]
    found += [(m.start(), m.group("fn") + "(...)") for m in _APOC_NODE_BUILDER_RE.finditer(masked)]
    return found


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


def whole_node_returns(cypher: str, procedures=()) -> list[str]:
    """Variables whose whole Sample node (or a path over samples) the query returns or collects: ``["s", ...]``.

    ``RETURN s``, ``RETURN *``, ``collect(s)`` anywhere, ``s {.*}``, ``properties(s)`` and ``nodes(p)``; a RETURN
    inside a CALL or EXISTS subquery is not sent to the caller and does not count. A Sample variable is one labelled
    ``Sample`` or ``T_<code>``, an end of DERIVED_FROM, the source of IN_STUDY or OF_TYPE, a fulltext hit, or a bare
    alias of one of these.

    ``procedures`` is the turn's variant allowlist (``cypher_text.variant_procedures``). When it names any procedure,
    a procedure's ``YIELD node`` / ``nodes`` / ``path`` count as well (``apoc.path.subgraphNodes(...) YIELD node
    RETURN node`` otherwise ships every attribute of every node it reaches), and so do the APOC functions that return
    a node or its properties (``apoc.any.properties(s)``, ``apoc.convert.toJson(s)``, ``apoc.agg.first(s)``,
    ``apoc.map.fromNodes(...)``, ...). A procedure's path may be read for names: ``[n IN nodes(path) | n.uuid]``,
    ``last(nodes(path)).uuid``, ``length(path)``. With no procedures the answer is exactly what it always was.
    """
    if not cypher or not cypher.strip():
        return []
    extra = cypher_text._extra(procedures)
    aware = bool(extra)
    scan = _scan(cypher, extra)
    masked = scan.masked
    shipped = scan.samples | scan.paths | scan.proc_paths | scan.node_lists
    found: list[tuple[int, str]] = []
    for regex, pool in ((_COLLECT_RE, shipped), (_PROPERTIES_RE, scan.samples), (_NODES_RE, scan.paths)):
        found += [(m.start(), m.group("var")) for m in regex.finditer(masked) if m.group("var") in pool]
    found += [(pos, var) for pos, var in scan.stars if var in scan.samples]
    if aware:
        found += _procedure_whole_nodes(scan)

    hidden = [(start, end) for start, end, kind in _brace_kinds(masked) if kind != "collect"]
    for keyword, kw_start, body_start, body_end in scan.clauses:
        if keyword != "RETURN" or any(start < kw_start < end for start, end in hidden):
            continue
        for s, e in _items(masked, body_start, body_end):
            item = _ITEM_ALIAS_RE.match(masked[s:e].strip())
            expr = item.group("expr").strip() if item else ""
            if expr == "*":
                starred = scan.samples | scan.proc_paths | scan.node_lists
                order = sorted(starred, key=lambda v: (re.search(rf"\b{re.escape(v)}\b", masked) or
                                                       re.search(r"$", masked)).start())
                found += [(s, var) for var in order]
            elif _NAME_RE.fullmatch(expr) and expr in shipped:
                found.append((s, expr))

    out: list[str] = []
    for _, var in sorted(found):
        if var not in out:
            out.append(var)
    return out


def _procedure_problems(cypher: str, procedures: frozenset[str]) -> list[str]:
    """``cypher_text.procedure_call_problems`` for a turn whose variant allows procedures; [] on every other turn."""
    return cypher_text.procedure_call_problems(cypher, procedures) if procedures else []


def _names(names, limit: int = 200) -> str:
    rendered = [n if _NAME_RE.fullmatch(n) else "`" + n.replace("`", "``") + "`" for n in sorted(names)]
    if len(rendered) > limit:
        rendered = rendered[:limit] + [f"and {len(rendered) - limit} more"]
    return ", ".join(rendered) if rendered else "(none)"


#: What every procedure-call repair says, after the problems themselves (cypher_text._apoc_path_reasons has the why).
_CALL_RULE = (
    "Every apoc.path call needs a literal configuration map with relationshipFilter naming only DERIVED_FROM "
    "('DERIVED_FROM>' walks to ancestors, '<DERIVED_FROM' to descendants), a literal integer maxLevel from 1 to "
    f"{cypher_text.APOC_PATH_MAX_LEVEL} (12 reaches every ancestor; use the number of steps the question names when "
    "it names one), and, on apoc.path.expandConfig, uniqueness: 'NODE_GLOBAL'. Or write the same lineage as a "
    "bounded pattern: (s)-[:DERIVED_FROM*1..12]->(a).")


def _call_lines(calls: list[str]) -> list[str]:
    """The repair lines for procedure-call problems (only a variant that allows procedures produces any)."""
    if not calls:
        return []
    return ["- procedure calls that cannot run as written: " + "; ".join(calls), _CALL_RULE]


def _catalog_repair_message(problems: list[_Problem], whole: list[str], snapshot, calls: list[str] = (),
                            shapes: list = ()) -> str:
    """The one repair prompt: every problem once, and the property names valid for each label involved.

    ``shapes`` are the query-shape guard's problems (P6a, P6b), repaired in the same round: alone they get their own
    message, beside catalog problems their lines join this one. With none, the message is exactly what it was.
    """
    if shapes and not (problems or whole or calls):
        return _shape_repair_message(list(shapes))
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
    lines += _call_lines(list(calls))
    lines += _shape_lines(list(shapes))
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


def _catalog_refusal(problems: list[_Problem], whole: list[str], calls: list[str] = (), shapes: list = ()) -> str:
    parts = []
    properties = [p.text for p in problems if p.kind == "property"]
    labels = [p.text for p in problems if p.kind == "label"]
    if properties:
        parts.append(f"properties {properties} are not in the catalog for those labels")
    if labels:
        parts.append(f"labels {labels} name no sample type")
    if whole:
        parts.append("it returns whole Sample nodes (" + ", ".join(f"whole node {v}" for v in whole) + ")")
    if calls:
        parts.append("procedure calls cannot run as written (" + "; ".join(calls) + ")")
    parts += _shape_refusal_parts(list(shapes))
    return "Graph agent could not produce valid Cypher; " + "; ".join(parts) + "."


# --------------------------------------------------------------------------- #
# The query-shape guards: P6a and P6b (NESSIE-MASTER-PLAN phase 9)
#
# Both refuse a shape, not a name, and both read only the Cypher text and the plan's parameters.
# graph_agent runs them in the catalog guard's single repair round, so one repair is re-checked by
# every guard at once and cannot trade one problem for another unchecked one.
#
# P6a, a variable-length path: `-[:DERIVED_FROM*1..8]->`, and the Cypher 5 quantified forms
# `-[:DERIVED_FROM]->{1,8}`, `->+` and `((a)-[:DERIVED_FROM]->(b)){1,8}`.
#   - A literal maximum from 1 to cypher_text.APOC_PATH_MAX_LEVEL passes when at least one end is
#     anchored (_WEAK or better, see below).
#   - No maximum, a parameter maximum or a maximum above it passes only when BOTH ends carry a sample
#     type or a pin (_STRONG): the expansion is then between two known sets rather than out of every
#     sample a filter happens to leave.
# The bound is 12, not 8: the longest DERIVED_FROM chain in the graph is 11 hops (the constant carries
# the measurement), so *1..12 reaches every ancestor and every descendant and the repair that complies
# truncates nothing.
#
# P6b, an unscoped fulltext call. The index analyses its term: it lowercases it, splits it at spaces
# and punctuation and matches each word anywhere in a sample's text. So a term of two or more words
# cannot keep them side by side, whether they are written side by side, joined by a hyphen or joined
# by AND, && or +, and unscoped the call answers from the whole database. It passes when its hits are
# scoped (_WEAK or better, a fulltext hit not counting as its own scope), or when the term is one word,
# one quoted phrase, or an explicit OR of those: an OR asks for the union and has no adjacency to lose.
# The term is resolved through a literal, a parameter, `WITH $q AS t`, `UNWIND $terms AS t`,
# `$terms[0]`, `+` concatenation and the case and trim functions. An unscoped call whose term is none
# of those is refused, not trusted: the guard cannot see what it would search.
#
# Anchors. _STRONG: a T_ label (in the pattern, or `WHERE a:T_X`), a `type` equality or IN, a uuid or
# id equality or IN, or the same keys in the pattern's map. _WEAK: any other comparison of a property
# with a value, an EXISTS or pattern predicate, a fulltext hit, a filtered relationship's ends, and a
# fixed-length neighbour of an anchored node. A variable carries what the query says about it anywhere,
# through a bare `WITH x AS a`, `a = b`, `a.uuid = b.uuid` and `(s {uuid: node.uuid})`. Nothing is an
# anchor under NOT, and `IS NOT NULL`, `IS NULL`, `<>`, `CONTAINS ''` and `=~ '.*'` narrow nothing. A
# disjunction anchors only what every branch anchors.
# --------------------------------------------------------------------------- #

_MAX_HOPS = cypher_text.APOC_PATH_MAX_LEVEL
_WEAK, _STRONG = 1, 2
_PIN_KEYS = frozenset({"uuid", "id"})
_FULLTEXT_PROCEDURE = "db.index.fulltext.querynodes"
_ARROW_LEFT_RE = re.compile(r"\s*<?\s*-\s*")
_ARROW_RIGHT_RE = re.compile(r"\s*-\s*>?\s*")
_QUANTIFIER = r"(?P<q>\{\s*(?P<lo>\d*)\s*(?P<comma>,)?\s*(?P<hi>\d*)\s*\}|\+|\*)"
# `(a)-[:R]->{1,8}(b)`, `(a)-->+(b)`: a quantifier between a relationship and the next node pattern.
_REL_QUANTIFIER_RE = re.compile(rf"(?<=\))(?P<arrow>\s*<?\s*-\s*(?:\[[^\[\]]*\]\s*)?-\s*>?)\s*{_QUANTIFIER}(?=\s*\()")
# `((a)-[:R]->(b)){1,8}`: a quantifier after a parenthesised path pattern.
_GROUP_QUANTIFIER_RE = re.compile(rf"\)\s*{_QUANTIFIER}")
_GROUP_ARROW_RE = re.compile(r"-\s*\[|->|<-|--")
_BOOLEAN_WORD_RE = re.compile(r"(AND|OR|XOR)\b", re.IGNORECASE)
_COMPARISON_RE = re.compile(r"=~|<>|!=|<=|>=|=|<|>|\bIN\b|\bCONTAINS\b|\bSTARTS\s+WITH\b|\bENDS\s+WITH\b"
                            r"|\bIS\s+NOT\s+NULL\b|\bIS\s+NULL\b", re.IGNORECASE)
_NARROWS_NOTHING = frozenset({"<>", "!=", "IS NOT NULL", "IS NULL"})
_STRING_TESTS = frozenset({"CONTAINS", "STARTS WITH", "ENDS WITH", "=~"})
_MATCH_ANYTHING = frozenset({".*", "(?i).*", "(?s).*", "^.*$", ".*?", "(?i)^.*$"})
_UNWRAP_RE = re.compile(r"(?P<fn>toLower|toUpper|lower|upper|trim|ltrim|rtrim|toString|coalesce)\s*\(", re.IGNORECASE)
_TERM_FUNCTIONS = frozenset({"TOLOWER", "TOUPPER", "LOWER", "UPPER", "TRIM", "LTRIM", "RTRIM"})
_LUCENE_WORD_RE = re.compile(r"\w+(?:[.'\u2019]\w+)*")  # \u2019 is the typographic apostrophe


class _Shape(NamedTuple):
    pos: int
    kind: str  # 'unbounded_path' | 'unanchored_path' | 'unscoped_fulltext'
    text: str  # the offending fragment, verbatim from the Cypher
    detail: str  # why it is one: the bound, the ends, the words the index would search


def _label_anchor(labels: str | None) -> int:
    """_STRONG for a label expression every alternative of which names a sample type (`:T_X`, `:T_X|T_Y`)."""
    if not labels or "!" in labels:
        return 0
    alternatives = [_label_names(alt) for alt in labels.split("|")]
    return _STRONG if alternatives and all(any(n.startswith("T_") for n in alt) for alt in alternatives) else 0


def _split_boolean(masked: str, start: int, end: int, words: frozenset[str]) -> list[tuple[int, int]]:
    """``masked[start:end]`` split at its depth-0 boolean operators named in ``words``."""
    spans, depth, item_start, i = [], 0, start, start
    while i < end:
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch.isalpha() or ch == "_":
            j = i
            while j < end and (masked[j].isalnum() or masked[j] == "_"):
                j += 1
            word = _BOOLEAN_WORD_RE.fullmatch(masked[i:j])
            if depth == 0 and word and word.group(1).upper() in words and (i == 0 or masked[i - 1] != "."):
                spans.append((item_start, i))
                item_start = j
            i = j
            continue
        i += 1
    spans.append((item_start, end))
    return spans


def _predicate_end(masked: str, start: int, stops: set[int]) -> int:
    """Where the WHERE predicate starting at ``start`` ends: the next clause at its depth, or its bracket's end."""
    depth = 0
    for i in range(start, len(masked)):
        if depth == 0 and i in stops:
            return i
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return i
            depth -= 1
        elif ch == "|" and depth == 0 and not re.search(rf":\s*!?\s*{_NAME}\s*$", masked[start:i]):
            return i  # `[x IN xs WHERE p | e]`, not a label disjunction
    return len(masked)


def _matching_open(masked: str, close: int) -> int:
    """Index of the `(` opening the `)` at ``close``, or -1."""
    depth = 0
    for j in range(close, -1, -1):
        if masked[j] == ")":
            depth += 1
        elif masked[j] == "(":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _strip(masked: str, original: str, s: int, e: int) -> tuple[int, int]:
    return cypher_text._strip_span(masked, original, s, e)


# A word that may stand right before a node pattern's `(`; any other word before it makes `(x)` a function's argument.
_PATTERN_WORDS = frozenset({"MATCH", "MERGE", "CREATE", "EXISTS", "WHERE", "AND", "OR", "XOR", "NOT",
                            "SHORTESTPATH", "ALLSHORTESTPATHS", "SHORTEST", "PATHS", "GROUPS"})


def _node_patterns(scan: _Scan) -> list[tuple[int, int, str, str | None, list[str]]]:
    """The scan's node elements that are node patterns: `(s:T_X)`, not the argument of `size(x)` or `count(node)`."""
    out = []
    for element in scan.elements:
        if element[2] != "node":
            continue
        word = re.search(r"(\w+)\s*$", scan.masked[:element[0]])
        if word and not word.group(1).isdigit() and word.group(1).upper() not in _PATTERN_WORDS:
            continue
        out.append(element)
    return out


def _clause_stops(scan: _Scan) -> set[int]:
    """Where each clause but WHERE starts; `STARTS WITH` and `ENDS WITH` are operators, not WITH clauses."""
    return {kw_start for keyword, kw_start, _, _ in scan.clauses if keyword != "WHERE"
            and not (keyword == "WITH" and re.search(r"\b(?:STARTS|ENDS)\s*$", scan.masked[:kw_start], re.I))}


class _Anchors:
    """How strongly each node pattern of one query is anchored: 0, _WEAK or _STRONG (see the section comment).

    ``fulltext_hits`` says whether a fulltext hit anchors its variable: yes for a path's end, no for the
    fulltext call's own scope.
    """

    def __init__(self, scan: _Scan, cypher: str, *, fulltext_hits: bool):
        self.scan, self.cypher, self.masked = scan, cypher, scan.masked
        masked = self.masked
        self.parent: dict[str, str] = {}
        direct: dict[str, int] = {}
        joins: list[tuple[str, str]] = []

        def lift(key, level):
            if level:
                direct[key] = max(direct.get(key, 0), level)

        self.calls = _procedure_yields(scan)
        self.nodes = _node_patterns(scan)
        self.node_vars = {e[3] for e in self.nodes if e[3]}
        self.node_vars |= {hit for *_, hit, _ in self.calls if hit}
        self.rel_vars = {e[3] for e in scan.elements if e[2] == "rel" and e[3]}
        sources: dict[str, set[str]] = {}
        for source, alias in scan.aliases:
            sources.setdefault(alias, set()).add(source)
        for _ in range(3):  # an alias of an alias, in any order
            for alias, found in sources.items():
                if len(found) == 1 and found <= self.node_vars and alias not in found:
                    self.node_vars.add(alias)
        for alias, found in sources.items():
            if len(found) == 1 and alias in self.node_vars and alias not in found:
                joins.append((alias, next(iter(found))))  # two sources (UNION branches) are two nodes, not one

        self.hops: set[int] = set()
        patterns = {e[0] for e in self.nodes}
        for start, end, kind, var, _names in scan.elements:
            key = self.key(start, var)
            if kind == "node" and start not in patterns:
                continue
            if kind == "node":
                m = _NODE_PATTERN_RE.match(masked, start)
                lift(key, _label_anchor(m.group("labels")) if m else 0)
                if m and m.group("props"):
                    for item_key, level, joined in self._map_items(m.start("props"), m.end("props")):
                        lift(key, level)
                        if joined:
                            joins.append((key, joined))
            else:
                m = _REL_PATTERN_RE.match(masked, start)
                if m and m.group("hops"):
                    self.hops.add(start)
                if m and m.group("props") and self._map_items(m.start("props"), m.end("props")):
                    lift(key, _WEAK)

        stops = _clause_stops(scan)
        for keyword, kw_start, body_start, _ in scan.clauses:
            if kw_start in stops or keyword != "WHERE":
                continue
            for var, level in self._predicate(body_start, _predicate_end(masked, body_start, stops), joins).items():
                lift(var, level)
        if fulltext_hits:
            for *_, hit, fulltext in self.calls:
                if hit and fulltext:
                    lift(hit, _WEAK)

        for a, b in joins:
            ra, rb = self.find(a), self.find(b)
            if ra != rb:
                self.parent[ra] = rb
        self.strength: dict[str, int] = {}
        for key, level in direct.items():
            root = self.find(key)
            self.strength[root] = max(self.strength.get(root, 0), level)
        self._propagate()

    @staticmethod
    def key(start: int, var: str | None) -> str:
        return var or f"#{start}"

    def find(self, key: str) -> str:
        while self.parent.get(key, key) != key:
            key = self.parent[key]
        return key

    def of(self, element) -> int:
        """The strength of a node pattern ``(start, end, 'node', var, labels)``; 0 for None."""
        return self.strength.get(self.find(self.key(element[0], element[3])), 0) if element else 0

    def _propagate(self) -> None:
        """A fixed-length neighbour of an anchored node, and either end of a filtered relationship, is _WEAK."""
        masked = self.masked
        patterns = {e[0] for e in self.nodes}
        elements = [e for e in self.scan.elements if e[2] == "rel" or e[0] in patterns]
        triples = []
        for i in range(1, len(elements) - 1):
            left, rel, right = elements[i - 1], elements[i], elements[i + 1]
            if left[2] != "node" or rel[2] != "rel" or right[2] != "node" or rel[0] in self.hops:
                continue
            if _ARROW_LEFT_RE.fullmatch(masked[left[1]:rel[0]]) and _ARROW_RIGHT_RE.fullmatch(masked[rel[1]:right[0]]):
                triples.append((self.key(left[0], left[3]), self.key(rel[0], rel[3]), self.key(right[0], right[3])))
        for _ in range(len(triples) + 1):
            changed = False
            for left, rel, right in triples:
                a, r, b = self.find(left), self.find(rel), self.find(right)
                for this, other in ((a, b), (b, a)):
                    if self.strength.get(this, 0) < _WEAK and (
                            self.strength.get(other, 0) >= _WEAK or self.strength.get(r, 0) >= _WEAK):
                        self.strength[this] = _WEAK
                        changed = True
            if not changed:
                return

    # ------------------------------------------------------------------ pattern maps

    def _map_items(self, start: int, end: int) -> list[tuple[str, int, str | None]]:
        """``(key, strength, joined variable)`` for each entry of the map whose braces span ``start:end``."""
        masked, cypher = self.masked, self.cypher
        out = []
        for s, e in _items(masked, start + 1, end - 1):
            i = _skip_blank(masked, cypher, s, e)
            name, j = _name_at(masked, cypher, i, e)
            while j < e and masked[j].isspace():
                j += 1
            if not name or j >= e or masked[j] != ":":
                continue
            vs, ve = _strip(masked, cypher, j + 1, e)
            if self._refs(vs, ve):
                same = self._identity(vs, ve)  # {uuid: node.uuid} is the same node, not a value
                out.append((name, 0, same[0] if same and name in _PIN_KEYS and same[1] == name else None))
            elif vs < ve and cypher_text._string_value(cypher[vs:ve]) != "":
                out.append((name, _STRONG if name in _PIN_KEYS or name == "type" else _WEAK, None))
        return out

    # ------------------------------------------------------------------ WHERE predicates

    def _refs(self, s: int, e: int) -> set[str]:
        """The node and relationship variables read in ``masked[s:e]`` (not property names, not functions)."""
        text = self.masked[s:e]
        found = set()
        for m in re.finditer(rf"(?<![\w.$])({_NAME})(?!\w)", text):
            if re.match(r"\s*\(", text[m.end():]):
                continue
            if m.group(1) in self.node_vars or m.group(1) in self.rel_vars:
                found.add(m.group(1))
        return found

    def _identity(self, s: int, e: int) -> tuple[str, str] | None:
        """``(var, 'node'|'uuid'|'id')`` when ``masked[s:e]`` is a node itself, its uuid or its id; else None."""
        text = self.masked[s:e].strip()
        m = (re.fullmatch(rf"({_NAME})", text) or re.fullmatch(rf"(?:id|elementId)\s*\(\s*({_NAME})\s*\)", text, re.I))
        if m and m.group(1) in self.node_vars:
            return m.group(1), "node"
        m = re.fullmatch(rf"({_NAME})\s*\.\s*({_NAME})", text)
        if m and m.group(1) in self.node_vars and m.group(2) in _PIN_KEYS:
            return m.group(1), m.group(2)
        return None

    def _property(self, s: int, e: int) -> str | None:
        """The property a comparison's subject reads, through the case, trim and coalesce functions; else None."""
        masked = self.masked
        s, e = _strip(masked, self.cypher, s, e)
        while s < e:
            m = _UNWRAP_RE.match(masked, s)
            if not m or cypher_text._matching_paren(masked, m.end() - 1) != e - 1:
                break
            s, e = _strip(masked, self.cypher, *_items(masked, m.end(), e - 1)[0])
        text = masked[s:e]
        if re.fullmatch(rf"(?:id|elementId)\s*\(\s*{_NAME}\s*\)", text, re.I):
            return "id"
        m = re.fullmatch(rf"{_NAME}\s*\.\s*({_NAME})", text)
        return m.group(1) if m else None

    def _narrows(self, s: int, e: int, op: str) -> bool:
        """Whether the value side ``s:e`` of a comparison can leave out anything at all."""
        s, e = _strip(self.masked, self.cypher, s, e)
        if s >= e:
            return False
        value = cypher_text._string_value(self.cypher[s:e])
        if op in _STRING_TESTS and value == "":
            return False
        return not (op == "=~" and value in _MATCH_ANYTHING)

    def _predicate(self, s: int, e: int, joins: list | None) -> dict[str, int]:
        """Anchors a boolean expression gives: a conjunction the most any part gives, a disjunction the least."""
        disjuncts = _split_boolean(self.masked, s, e, frozenset({"OR", "XOR"}))
        if len(disjuncts) > 1:
            results = [self._predicate(ds, de, None) for ds, de in disjuncts]
            common = set.intersection(*(set(r) for r in results))
            return {var: min(r[var] for r in results) for var in common}
        out: dict[str, int] = {}
        for cs, ce in _split_boolean(self.masked, s, e, frozenset({"AND"})):
            for var, level in self._atom(cs, ce, joins).items():
                out[var] = max(out.get(var, 0), level)
        return out

    def _atom(self, s: int, e: int, joins: list | None) -> dict[str, int]:
        masked = self.masked
        s, e = _strip(masked, self.cypher, s, e)
        text = masked[s:e]
        if not text.strip() or re.match(r"NOT\b", text, re.IGNORECASE):
            return {}
        if text[0] == "(" and cypher_text._matching_paren(masked, s) == e - 1:
            return self._predicate(s + 1, e - 1, joins)
        if re.match(r"(?:EXISTS|COUNT)\s*\{", text, re.IGNORECASE) or any(
                kind == "rel" and s <= start < e for start, _, kind, _, _ in self.scan.elements):
            return {var: _WEAK for var in self._refs(s, e) if var in self.node_vars}
        label = re.fullmatch(rf"(?P<var>{_NAME})\s*(?P<labels>{_LABEL_EXPR})", text)
        if label:
            level = _label_anchor(label.group("labels")) if label.group("var") in self.node_vars else 0
            return {label.group("var"): level} if level else {}
        op = next((m for m in _COMPARISON_RE.finditer(masked, s, e) if _depth(masked[s:m.start()], m.start() - s) == 0),
                  None)
        if op is None:
            return {}
        name = " ".join(op.group(0).upper().split())
        if name in _NARROWS_NOTHING:
            return {}
        left, right = (s, op.start()), (op.end(), e)
        lrefs, rrefs = self._refs(*left), self._refs(*right)
        if lrefs and rrefs:
            a, b = self._identity(*left), self._identity(*right)
            if name == "=" and joins is not None and a and b and a[1] == b[1]:
                joins.append((a[0], b[0]))
            return {}
        refs = lrefs or rrefs
        if len(refs) != 1:
            return {}
        var = next(iter(refs))
        subject, value = (left, right) if lrefs else (right, left)
        if not self._narrows(*value, name):
            return {}
        if var not in self.node_vars:
            return {var: _WEAK}  # a filtered relationship: _propagate anchors its ends
        prop = self._property(*subject)
        pinned = prop in _PIN_KEYS or prop == "type"
        if pinned and (name == "=" or (name == "IN" and subject == left)):
            return {var: _STRONG}
        return {var: _WEAK}


def _procedure_yields(scan: _Scan) -> list[tuple[int, int, int, str | None, bool]]:
    """Per procedure call: the positions of CALL, `(` and `)`, the yielded node variable or None, and whether the
    call is the fulltext procedure."""
    masked = scan.masked
    out = []
    for m in _PROC_CALL_RE.finditer(masked):
        close = cypher_text._matching_paren(masked, m.end() - 1)
        if close == -1:
            continue
        hit = None
        yielded = re.match(r"\s*YIELD\b", masked[close + 1:], re.IGNORECASE)
        if yielded:
            start = close + 1 + yielded.end()
            stop = _YIELD_END_RE.search(masked, start)
            for s, e in _items(masked, start, stop.start() if stop else len(masked)):
                item = _YIELD_ITEM_RE.match(masked[s:e].strip())
                if item and item.group("field").lower() in ("node", "*"):
                    hit = item.group("alias") or "node"
        out.append((m.start(), m.end() - 1, close, hit, m.group("name").lower() == _FULLTEXT_PROCEDURE))
    return out


# ------------------------------------------------------------------------------- P6a: the paths


def _hop_range(hops: str) -> tuple[int, int | None] | None:
    """``(min, max)`` for `*`, `*3`, `*1..8`, `*..8` or `*2..`; max None when there is none. None when unreadable."""
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


def _quantifier_range(m: re.Match) -> tuple[int, int | None] | None:
    if m.group("q") == "+":
        return 1, None
    if m.group("q") == "*":
        return 0, None
    low, high = m.group("lo"), m.group("hi")
    if not m.group("comma"):
        return (int(low), int(low)) if low else None
    return (int(low) if low else 0), (int(high) if high else None)


def _variable_paths(scan: _Scan, cypher: str):
    """Every variable-length path: ``(pos, fragment, (min, max), max is a parameter, left end, right end)``."""
    masked = scan.masked
    nodes = _node_patterns(scan)
    by_end = {e[1]: e for e in nodes}
    by_start = {e[0]: e for e in nodes}

    def before(pos):
        k = pos
        while k > 0 and masked[k - 1].isspace():
            k -= 1
        return by_end.get(k)

    def after(pos):
        k = pos
        while k < len(masked) and masked[k].isspace():
            k += 1
        return by_start.get(k)

    def fragment(s, e):
        return " ".join(cypher[s:e].split())

    for m in _REL_PATTERN_RE.finditer(masked):
        k = m.start() - 1
        while k >= 0 and masked[k].isspace():
            k -= 1
        if not m.group("hops") or k < 0 or masked[k] != "-":
            continue
        bounds = _hop_range(m.group("hops"))
        if bounds is None:
            continue
        left = next((n for n in reversed(nodes) if n[1] <= m.start()
                     and _ARROW_LEFT_RE.fullmatch(masked[n[1]:m.start()])), None)
        right = next((n for n in nodes if n[0] >= m.end() and _ARROW_RIGHT_RE.fullmatch(masked[m.end():n[0]])), None)
        yield m.start(), fragment(m.start(), m.end()), bounds, "$" in cypher[m.start("hops"):m.end("hops")], left, right
    for m in _REL_QUANTIFIER_RE.finditer(masked):
        bounds = _quantifier_range(m)
        if bounds is not None:
            yield (m.start("arrow"), fragment(m.start("arrow"), m.end()), bounds, False,
                   by_end.get(m.start()), after(m.end()))
    for m in _GROUP_QUANTIFIER_RE.finditer(masked):
        open_ = _matching_open(masked, m.start())
        inner = masked[open_ + 1:m.start()] if open_ != -1 else ""
        bounds = _quantifier_range(m)
        if bounds is None or not inner.lstrip().startswith("(") or not _GROUP_ARROW_RE.search(inner):
            continue
        inside = [n for n in nodes if open_ < n[0] and n[1] <= m.start()]
        left = before(open_) or (inside[0] if inside else None)
        right = after(m.end()) or (inside[-1] if inside else None)
        yield open_, fragment(open_, m.end()), bounds, False, left, right


def _path_problems(scan: _Scan, cypher: str) -> list[_Shape]:
    """P6a: every variable-length path whose bound and ends cannot run safely (the section comment has the rule)."""
    anchors = _Anchors(scan, cypher, fulltext_hits=True)
    problems: list[_Shape] = []

    def end_text(end) -> str:
        return " ".join(cypher[end[0]:end[1]].split()) if end else "an unnamed end"

    for pos, text, (_low, high), parameter, left, right in _variable_paths(scan, cypher):
        levels = anchors.of(left), anchors.of(right)
        if high is not None and high <= _MAX_HOPS and not parameter:
            if max(levels) == 0:
                problems.append(_Shape(pos, "unanchored_path", text,
                                       f"neither end ({end_text(left)} and {end_text(right)}) has a sample type, a "
                                       "pinned uuid or a predicate that narrows it"))
            continue
        if min(levels) >= _STRONG:
            continue
        if parameter:
            bound = "has a parameter for its maximum, which cannot be checked before the query runs"
        elif high is None:
            bound = "has no maximum hop count"
        else:
            bound = f"has a maximum of {high} hops, above {_MAX_HOPS}"
        strong = [end_text(end) for end, level in zip((left, right), levels) if level >= _STRONG]
        ends = (f"only {strong[0]} is anchored by a sample type or a pinned uuid" if strong
                else "neither end is anchored by a sample type or a pinned uuid")
        problems.append(_Shape(pos, "unbounded_path", text, f"{bound}, and {ends}"))
    return problems


# ------------------------------------------------------------------------------ P6b: fulltext calls


def _lucene_items(text: str) -> list[tuple[str, str]]:
    """The top-level items of a Lucene query string.

    ``('word'|'phrase'|'group', text)``, ``('op', 'AND'|'OR'|'NOT')`` and ``('prefix', '+'|'-'|'!')``.
    """
    items: list[tuple[str, str]] = []
    i, n = 0, len(text)

    def skip_suffix(j):  # a boost or a slop: ^2, ~3
        m = re.match(r"[~^][\d.]*", text[j:])
        return j + (m.end() if m else 0)

    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            items.append(("phrase", text[i + 1:j]))
            i = skip_suffix(j + 1)
        elif c == "(":
            depth, j, quoted = 0, i, False
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    quoted = not quoted
                elif not quoted and text[j] == "(":
                    depth += 1
                elif not quoted and text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            items.append(("group", text[i + 1:j]))
            i = skip_suffix(j + 1)
        elif text.startswith("&&", i) or text.startswith("||", i):
            items.append(("op", "AND" if c == "&" else "OR"))
            i += 2
        elif c in "+-!":
            items.append(("prefix", c))
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 2 if text[j] == "\\" else 1
            word = text[i:j]
            items.append(("op", word) if word in ("AND", "OR", "NOT") else ("word", word))
            i = j
    return items


def _analysed_words(text: str) -> list[str]:
    """The words the index's analyser makes of ``text``: lowercased, split at spaces and punctuation."""
    return [w.lower() for w in _LUCENE_WORD_RE.findall(text)]


def _unit_words(kind: str, text: str) -> list[str]:
    if kind == "word":
        text = re.sub(rf"^{_NAME}:(?=\S)", "", text)  # a field prefix, search_text:foo
        text = re.sub(r"[~^][\d.]*$", "", text)  # a fuzzy or boost suffix, foo~2, foo^2
        if re.search(r"(?<!\\)[*?]", text):
            return [text.lower()]  # a wildcard term is not analysed: it stays one term
    return _analysed_words(text)


def _splits_words(term: str) -> bool:
    """Whether the index would search ``term`` as separate words, losing their adjacency.

    It would not for one word, one quoted phrase, or an explicit OR of those. It would for two words side by side,
    one word the analyser splits (a hyphen, a slash), and any AND, &&, +, NOT, - or ! combination of two units.
    """
    items = _lucene_items(term)
    units = [(kind, text) for kind, text in items if kind in ("word", "phrase", "group")]
    if len(units) == 1:
        kind, text = units[0]
        return _splits_words(text) if kind == "group" else (kind == "word" and len(_unit_words(kind, text)) > 1)
    if not units:
        return False
    gaps: list[list[tuple[str, str]]] = []
    for kind, text in items:
        if kind in ("word", "phrase", "group"):
            gaps.append([])
        elif gaps:
            gaps[-1].append((kind, text))
    if any(kind == "prefix" for kind, _ in items) or any(gap != [("op", "OR")] for gap in gaps[:-1]) or gaps[-1]:
        return True
    return any(_splits_words(text) if kind == "group" else (kind == "word" and len(_unit_words(kind, text)) > 1)
               for kind, text in units)


def _term_words(term: str) -> list[str]:
    words = [w for kind, text in _lucene_items(term) if kind in ("word", "phrase", "group")
             for w in (_term_words(text) if kind == "group" else _unit_words(kind, text))]
    return list(dict.fromkeys(words))


def _term_bindings(scan: _Scan, name: str) -> list[tuple[str, int, int]]:
    """``(WITH|UNWIND, expression start, expression end)`` for each `... AS name` in a WITH or UNWIND clause."""
    masked, out, stops = scan.masked, [], _clause_stops(scan)
    for keyword, kw_start, body_start, body_end in scan.clauses:
        if keyword not in ("WITH", "UNWIND") or kw_start not in stops:
            continue
        for s, e in _items(masked, body_start, body_end):
            m = re.search(rf"\bAS\s+({_NAME})\s*$", masked[s:e], re.IGNORECASE)
            if m and m.group(1) == name:
                d = re.match(r"\s*DISTINCT\b", masked[s:s + m.start()], re.IGNORECASE)
                out.append((keyword, s + (d.end() if d else 0), s + m.start()))
    return out


def _split_plus(masked: str, s: int, e: int) -> list[tuple[int, int]]:
    spans, depth, start = [], 0, s
    for i in range(s, e):
        if masked[i] in "([{":
            depth += 1
        elif masked[i] in ")]}":
            depth -= 1
        elif masked[i] == "+" and depth == 0:
            spans.append((start, i))
            start = i + 1
    spans.append((start, e))
    return spans


def _term_values(scan: _Scan, cypher: str, parameters, s: int, e: int, depth: int = 0) -> list[str] | None:
    """Every string the expression ``cypher[s:e]`` can be, or None when the guard cannot read it."""
    masked = scan.masked
    s, e = _strip(masked, cypher, s, e)
    if s >= e or depth > 8:
        return None
    original = cypher[s:e]
    literal = cypher_text._string_value(original)
    if literal is not None:
        return [literal]
    if masked[s] == "(" and cypher_text._matching_paren(masked, s) == e - 1:
        return _term_values(scan, cypher, parameters, s + 1, e - 1, depth + 1)
    parts = _split_plus(masked, s, e)
    if len(parts) > 1:
        values = [""]
        for ps, pe in parts:
            got = _term_values(scan, cypher, parameters, ps, pe, depth + 1)
            if got is None:
                return None
            values = [v + g for v in values for g in got][:64]
        return values
    param = re.fullmatch(rf"\$({_NAME})(?:\s*\[\s*(-?\d+)\s*\])?", original)
    if param:
        value = parameters.get(param.group(1)) if isinstance(parameters, dict) else None
        if param.group(2) is not None:
            if not isinstance(value, list) or not -len(value) <= int(param.group(2)) < len(value):
                return None
            value = value[int(param.group(2))]
        return [value] if isinstance(value, str) else None
    call = re.match(rf"({_NAME})\s*\(", masked[s:e])
    if call and call.group(1).upper() in _TERM_FUNCTIONS and \
            cypher_text._matching_paren(masked, s + call.end() - 1) == e - 1:
        return _term_values(scan, cypher, parameters, s + call.end(), e - 1, depth + 1)
    if _NAME_RE.fullmatch(masked[s:e]) and masked[s:e] == original:
        bindings = _term_bindings(scan, original)
        values: list[str] = []
        for keyword, bs, be in bindings:
            got = (_term_values(scan, cypher, parameters, bs, be, depth + 1) if keyword == "WITH"
                   else _list_values(scan, cypher, parameters, bs, be, depth + 1))
            if got is None:
                return None
            values += got
        return values or None
    return None


def _list_values(scan: _Scan, cypher: str, parameters, s: int, e: int, depth: int) -> list[str] | None:
    """Every element of the list expression an UNWIND reads, or None when the guard cannot read it."""
    masked = scan.masked
    s, e = _strip(masked, cypher, s, e)
    if s >= e:
        return None
    param = re.fullmatch(rf"\$({_NAME})", cypher[s:e])
    if param:
        value = parameters.get(param.group(1)) if isinstance(parameters, dict) else None
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        return list(value) if ok else None
    if masked[s] == "[" and _matching_close_bracket(masked, s) == e - 1:
        values: list[str] = []
        for is_, ie in _items(masked, s + 1, e - 1):
            got = _term_values(scan, cypher, parameters, is_, ie, depth + 1)
            if got is None:
                return None
            values += got
        return values
    return None


def _fulltext_problems(scan: _Scan, cypher: str, parameters) -> list[_Shape]:
    """P6b: every fulltext call whose hits are unscoped and whose term the index would split into separate words."""
    masked = scan.masked
    anchors = None
    problems: list[_Shape] = []
    for start, open_, close, hit, fulltext in _procedure_yields(scan):
        if not fulltext:
            continue
        if hit is not None:
            anchors = anchors or _Anchors(scan, cypher, fulltext_hits=False)
            if anchors.strength.get(anchors.find(hit), 0) >= _WEAK:
                continue
        text = " ".join(cypher[start + len("CALL"):close + 1].split())
        arguments = _items(masked, open_ + 1, close)
        terms = _term_values(scan, cypher, parameters, *arguments[1]) if len(arguments) >= 2 else None
        if terms is None:
            problems.append(_Shape(start, "unscoped_fulltext", text,
                                   "has a search term that cannot be read before the query runs, and nothing scopes "
                                   "its hits to a sample type or a filter"))
            continue
        split = [term for term in terms if _splits_words(term)]
        if split:
            words = ", ".join(repr(w) for w in _term_words(split[0]))
            problems.append(_Shape(start, "unscoped_fulltext", text,
                                   f"searches the term {split[0]!r} as the separate words {words}, each anywhere in a "
                                   "sample's text, and nothing scopes its hits to a sample type or a filter"))
    return problems


# ------------------------------------------------------------------------------ the guard's surface


def query_shape_problems(cypher: str | None, parameters=None) -> list[_Shape]:
    """P6a and P6b: the shapes this graph must not be asked to run, in the order they appear in the Cypher.

    ``parameters`` is the plan's parameter map, which the fulltext check reads the search term from. A guard that
    breaks on a query says nothing about it rather than refusing it.
    """
    if not cypher or not cypher.strip():
        return []
    try:
        scan = _scan(cypher)
        problems = _path_problems(scan, cypher) + _fulltext_problems(scan, cypher, parameters)
    except Exception as e:  # noqa: BLE001 (never refuse a query because the guard itself broke)
        print(f"[DEBUG][GRAPH][SHAPE_GUARD] guard failed, allowing the query: {e!r}")
        return []
    return sorted(problems)


def refused_query_shapes(cypher: str | None, parameters=None) -> list[str]:
    """One line per problem: ``["unbounded path [:DERIVED_FROM*]: has no maximum hop count, ..."]``."""
    return [f"{p.kind.replace('_', ' ')} {p.text}: {p.detail}" for p in query_shape_problems(cypher, parameters)]


def _shape_lines(shapes: list[_Shape]) -> list[str]:
    """The repair lines for shape problems: each problem, then the rule for each kind present."""
    if not shapes:
        return []
    lines = [f"- {p.text}: {p.detail}" for p in shapes]
    kinds = {p.kind for p in shapes}
    if "unbounded_path" in kinds:
        lines.append(
            f"Give every variable-length path a literal maximum from 1 to {_MAX_HOPS}: "
            f"`[:DERIVED_FROM*1..{_MAX_HOPS}]`, "
            f"or `*0..{_MAX_HOPS}` when the starting sample itself counts; use the number of steps the question names "
            f"when it names one. The longest DERIVED_FROM chain in this graph is 11 hops, so *1..{_MAX_HOPS} reaches "
            f"every ancestor and every descendant. A path with no maximum, or a maximum above {_MAX_HOPS}, runs only "
            "when both of its ends are anchored by a sample type (`(d:T_D_SEQ)` or `d.type = $type`) or a pinned "
            "uuid (`{uuid: $uid}` or `d.uuid IN $uids`).")
    if "unanchored_path" in kinds:
        lines.append(
            "Anchor at least one end of the path: a sample type (`(d:T_D_SEQ)`), a pinned uuid (`{uuid: $uid}`), or "
            "a predicate that compares a property of that end with a value. `IS NOT NULL`, `IS NULL`, `<>` and a "
            "predicate under NOT narrow nothing, and plain `(:Sample)` ends expand from every sample in the graph.")
    fulltext = [p for p in shapes if p.kind == "unscoped_fulltext"]
    if fulltext:
        lines.append(
            "The fulltext index matches whole words: it lowercases the term, splits it at spaces and punctuation, and "
            "matches each word separately, anywhere in a sample's text. Unscoped, a term of two or more words (side "
            "by side, joined by a hyphen, or joined by AND, && or +) cannot keep them side by side and is answered "
            "from the whole database. Do one of these instead:\n"
            "  - match the text directly, which keeps adjacency and punctuation and also matches inside longer words: "
            "WHERE toLower(s.search_text) CONTAINS toLower($term), one per word or phrase, joined by AND or OR as the "
            "question means;\n"
            "  - or keep the index and scope its hits to the sample type the question is about: "
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node WHERE node:T_<code>.\n"
            "An unscoped call is fine for one word, one quoted phrase (\"...\" inside the term), or an explicit OR of "
            "those.")
        if any("cannot be read" in p.detail for p in fulltext):
            lines.append("Pass the search term as one parameter the plan binds, or as a literal string, so it can be "
                         "checked before the query runs.")
    return lines


def _shape_repair_message(shapes: list[_Shape]) -> str:
    """The one repair prompt when the only problems are shapes."""
    return "\n".join(["The previous Cypher is well formed, but its shape cannot run safely on this graph:"]
                     + _shape_lines(shapes)
                     + ["Regenerate the Cypher, or return an empty cypher and say why if the question cannot be "
                        "answered from the graph."])


def _shape_refusal_parts(shapes: list[_Shape]) -> list[str]:
    parts = []
    for p in shapes:
        if p.kind == "unbounded_path":
            parts.append(f"the path {p.text} {p.detail}; a path runs with a maximum from 1 to {_MAX_HOPS} or with "
                         f"both ends anchored (bound it at *1..{_MAX_HOPS})")
        elif p.kind == "unanchored_path":
            parts.append(f"the path {p.text} expands from every sample: {p.detail}")
        else:
            parts.append(f"the unscoped fulltext call {p.text} {p.detail}")
    return parts


def _shape_refusal(shapes: list[_Shape]) -> str:
    """The user-facing reason, when the one repair kept a refused shape."""
    return "Graph agent could not produce valid Cypher; " + "; ".join(_shape_refusal_parts(shapes)) + "."


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


def _variant_structure(config) -> str | None:
    """An evaluation prompt variant's graph_schema_structure.txt text, or None for the file (prompt_variants.py).

    Type-checked because tests hand the graph agent a MagicMock config, whose every attribute exists.
    """
    structure = getattr(config, "GRAPH_SCHEMA_STRUCTURE", None)
    return structure if isinstance(structure, str) else None


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
        schema = graph_context.render_graph_context(snapshot, details, structure=_variant_structure(config))
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
        schema = graph_context.render_graph_context(snapshot, details, structure=_variant_structure(config))
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
    """The committed protocol and assay-connection blocks, gated and bounded as on the catalog path.

    The gate is ``graph_context.mentions`` and the bound ``graph_context.fit_vocabulary``: the blocks together stay
    within ``VOCAB_BUDGET_BYTES``, and an entry sharing a word with the question is never cut for one that does not.
    """
    blocks = []
    query = user_query or ""
    if graph_context.mentions(graph_context.PROTOCOL_WORDS, query) and getattr(config, "PROTOCOL_SCHEMA", None):
        protocol_titles = config.PROTOCOL_SCHEMA.get("protocol_titles", [])
        if protocol_titles:
            blocks.append(graph_context.json_list_block("PROTOCOL VOCABULARY (DERIVED_FROM.protocol_title values):",
                                                        protocol_titles))
            print(f"[DEBUG][GRAPH] Including protocol vocabulary ({len(protocol_titles)} titles)")
    if graph_context.mentions(graph_context.ASSAY_WORDS, query) and getattr(config, "ASSAY_SAMPLE_CONNECTIONS", None):
        connections = config.ASSAY_SAMPLE_CONNECTIONS.get("connections", [])
        if connections:
            blocks.append(graph_context.json_list_block(
                "ASSAY-SAMPLE CONNECTIONS (assay → parent_type → child_type, use to determine which side a sample "
                "type sits on for a given assay):", connections))
            print(f"[DEBUG][GRAPH] Including assay-sample connections ({len(connections)} entries)")
    return graph_context.fit_vocabulary(blocks, query)


# --------------------------------------------------------------------------- #
# P5 (PilotAPOC/review/PROPOSALS.md): what of the parser plan the graph agent sees
# --------------------------------------------------------------------------- #
#
# Off by default. An evaluation prompt variant turns it on with ``project_parser_plan``
# (prompt_variants.py sets PROJECT_PARSER_PLAN on the per-request config copy).
#
# Measured on the 60 reviewed graph-arm turns of run full-a (PilotAPOC/runs/full-a/graph/
# payloads/<id>/main.json, query_complete.debug.parser_plan, which is the plan graph_agent
# received): the parser writes its REST reasoning into three fields.
#   notes                41 of 60 name advanced_search as the right endpoint, 15 assert that no
#                        relationship (or lineage, or graph) traversal is needed
#   endpoint_candidates  52 of 60 list a REST path, /nextseek_api/samples/advanced_search/
#   intent_summary       1 of 60 carries REST prose ("via advanced_search ... since REST cannot
#                        enforce numeric comparisons"); the rest paraphrase the question, which
#                        the graph agent already receives verbatim as the user message
# The rest carry nothing the graph agent can use (target_endpoint, previous_api_plan and metadata
# were empty on all 60): target_endpoint is a REST path by definition, previous_api_plan is a REST
# request body, target_result_id is the memory path's bundle id, report_mode and report_type are
# the reporter's, metadata holds the parser's failure record, mode is graph_query whenever this
# runs, and previous_user_query duplicates the prior query a graph refine already carries in its
# refine_context (orchestrator._build_graph_refine_context).
# What the graph agent needs from the plan is what the parser resolved: the entities and the
# filters. Those are kept whole.
PARSER_PLAN_KEPT: tuple[str, ...] = ("resolved", "filters")
PARSER_PLAN_DROPPED: tuple[str, ...] = (
    "mode", "target_endpoint", "intent_summary", "notes", "endpoint_candidates",
    "previous_api_plan", "previous_user_query", "target_result_id", "report_mode", "report_type", "metadata",
)
PROJECTED_PLAN_HEADING = "RESOLVED ENTITIES AND FILTERS (from the Parser Agent):"


def project_parser_plan(plan_dict: dict) -> dict:
    """The parser plan cut down to ``PARSER_PLAN_KEPT``: the resolved entities and the filters."""
    return {name: plan_dict[name] for name in PARSER_PLAN_KEPT if name in plan_dict}


def _projects_parser_plan(config) -> bool:
    # `is True`, not truthiness: tests hand the graph agent a MagicMock config, whose every attribute exists.
    return getattr(config, "PROJECT_PARSER_PLAN", False) is True


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
    if plan_dict and _projects_parser_plan(config):
        upstream_context = PROJECTED_PLAN_HEADING + "\n" + json.dumps(project_parser_plan(plan_dict), indent=2)
    elif plan_dict:
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
    # An evaluation prompt variant's allowed procedures (v2_apoc); empty on every other turn, which leaves the
    # guards below exactly as they were.
    procedures = cypher_text.variant_procedures(config)

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
            # A variant that allows procedures adds the procedure guard to the same round, and the query-shape
            # guard (P6a, P6b) joins it on every turn. The repair is re-checked by all of them, so a repair
            # cannot trade one problem for another unchecked one.
            problems = _property_problems(result.cypher, catalog.snapshot)
            whole = whole_node_returns(result.cypher, procedures)
            calls = _procedure_problems(result.cypher, procedures)
            shapes = query_shape_problems(result.cypher, result.parameters)
            if problems or whole or calls or shapes:
                print(f"[DEBUG][GRAPH] Catalog guard: {[p.text for p in problems]} whole nodes {whole}"
                      + (f" calls {calls}" if calls else "") + (f" shapes {[p.kind for p in shapes]}" if shapes else "")
                      + "; attempting repair")
                messages.append({"role": "system", "content": _catalog_repair_message(
                    problems, whole, catalog.snapshot, calls, shapes)})
                result = call("Regenerate the Cypher.", "graph_agent_repair")
                result.cypher, _ = canonicalize_sample_uid_property(result.cypher)
                print(f"[DEBUG][GRAPH] Repaired cypher: {result.cypher!r}")
                problems = _property_problems(result.cypher, catalog.snapshot)
                whole = whole_node_returns(result.cypher, procedures)
                calls = _procedure_problems(result.cypher, procedures)
                shapes = query_shape_problems(result.cypher, result.parameters)
                if problems or whole or calls or shapes:
                    print(f"[DEBUG][GRAPH] Repair still fails the catalog guard: {[p.text for p in problems]} "
                          f"whole nodes {whole}" + (f" calls {calls}" if calls else "")
                          + (f" shapes {[p.kind for p in shapes]}" if shapes else "") + "; returning empty plan")
                    return GraphAgentPlan(cypher="", explanation=_catalog_refusal(problems, whole, calls, shapes),
                                          parameters={}, context_mode=context_mode)
        else:
            # Schema guard: reject Cypher that filters on properties no node actually has
            # (e.g. a hallucinated `s.Lab`). Re-prompt once with the error + valid props;
            # if the repair still references unknown properties, return a graceful empty plan
            # rather than running a query that can only match nothing. A variant that allows
            # procedures adds the procedure guard to the same round, the query-shape guard
            # (P6a, P6b) joins it on every turn, and the repair is re-checked by all of them.
            known = known_node_properties(config.NEO4J_SCHEMA) | known_relationship_properties(config.NEO4J_SCHEMA)
            unknown = unknown_cypher_properties(result.cypher, known)
            calls = _procedure_problems(result.cypher, procedures)
            shapes = query_shape_problems(result.cypher, result.parameters)
            if unknown or calls or shapes:
                print(f"[DEBUG][GRAPH] Unknown properties in cypher: {unknown}"
                      + (f" calls {calls}" if calls else "") + (f" shapes {[p.kind for p in shapes]}" if shapes else "")
                      + "; attempting repair")
                parts = []
                if unknown:
                    parts.append(
                        f"The previous Cypher referenced properties that do not exist on any node or relationship: "
                        f"{unknown}. Valid properties are: {sorted(known)}. "
                        "Regenerate the Cypher using ONLY existing properties, or return an empty "
                        "cypher if the question cannot be answered from the graph."
                    )
                if calls:
                    parts.append("\n".join(["The previous Cypher cannot run as written:"] + _call_lines(calls)))
                if shapes:
                    parts.append(_shape_repair_message(shapes))
                messages.append({"role": "system", "content": "\n".join(parts)})
                result = call("Regenerate the Cypher.", "graph_agent_repair")
                print(f"[DEBUG][GRAPH] Repaired cypher: {result.cypher!r}")
                still = unknown_cypher_properties(result.cypher, known)
                calls = _procedure_problems(result.cypher, procedures)
                shapes = query_shape_problems(result.cypher, result.parameters)
                if still or calls or shapes:
                    print(f"[DEBUG][GRAPH] Repair still references unknown properties: {still}"
                          + (f" calls {calls}" if calls else "")
                          + (f" shapes {[p.kind for p in shapes]}" if shapes else "") + "; returning empty plan")
                    reasons = []
                    if still:
                        reasons.append(f"properties {still} do not exist on any node in the schema")
                    if calls:
                        reasons.append("procedure calls cannot run as written (" + "; ".join(calls) + ")")
                    reasons += _shape_refusal_parts(shapes)
                    return GraphAgentPlan(
                        cypher="",
                        explanation="Graph agent could not produce valid Cypher; " + "; ".join(reasons) + ".",
                        parameters={},
                        context_mode=context_mode,
                    )

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

from __future__ import annotations

import json
import re
from typing import Any

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


def _mask_cypher(cypher: str) -> str:
    """
    Blank out string literals, backtick identifiers, `$params` and comments,
    replacing each character with a space so every offset still lines up with the
    original text. Clause scanning runs on the mask; slicing runs on the original.
    """
    out = list(cypher)
    i, n = 0, len(cypher)
    while i < n:
        ch = cypher[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if cypher[j] == "\\":
                    j += 2
                    continue
                if cypher[j] == ch:
                    break
                j += 1
            end = min(j + 1, n)
        elif ch == "$":
            j = i + 1
            while j < n and (cypher[j].isalnum() or cypher[j] == "_"):
                j += 1
            end = j
        elif cypher.startswith("//", i):
            j = cypher.find("\n", i)
            end = n if j == -1 else j
        elif cypher.startswith("/*", i):
            j = cypher.find("*/", i + 2)
            end = n if j == -1 else j + 2
        else:
            i += 1
            continue
        for k in range(i, end):
            out[k] = " "
        i = max(end, i + 1)
    return "".join(out)


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


def graph_agent(
    config: ChatConfig,
    user_query: str,
    entity_result: EntityAgentOutput | dict,
    parser_plan: ParserPlan | dict | None = None,
    retry_context: str | None = None,
    refine_context: str | None = None,
) -> GraphAgentPlan:
    """
    Generate a Cypher query for the given user query using the live graph schema.
    Receives resolved entities from the Entity Agent and optional parser filters,
    then calls the LLM to produce a GraphAgentPlan (cypher + explanation + parameters).
    Falls back to an empty plan on structured-output failure.
    """
    print("\n[DEBUG][GRAPH] User query:", user_query)

    entity_dict = entity_result.model_dump() if hasattr(entity_result, "model_dump") else entity_result
    plan_dict = parser_plan.model_dump() if hasattr(parser_plan, "model_dump") else (parser_plan or {})

    schema_json = json.dumps(config.NEO4J_SCHEMA, indent=2) if config.NEO4J_SCHEMA else "{}"

    # Conditionally include protocol vocabulary if query mentions protocol-related terms
    protocol_keywords = ("protocol", "method", "procedure", "technique")
    include_protocol = any(kw in user_query.lower() for kw in protocol_keywords)
    protocol_context = ""
    if include_protocol and getattr(config, "PROTOCOL_SCHEMA", None):
        protocol_titles = config.PROTOCOL_SCHEMA.get("protocol_titles", [])
        if protocol_titles:
            protocol_context = "PROTOCOL VOCABULARY (DERIVED_FROM.protocol_title values):\n" + json.dumps(protocol_titles, indent=2)
            print(f"[DEBUG][GRAPH] Including protocol vocabulary ({len(protocol_titles)} titles)")

    # Conditionally include assay-sample connections when query involves assays or data types
    assay_conn_keywords = ("assay", "sequencing", "cytometry", "spectrometry", "imaging", "data", "processed", "associated", "underwent", "via", "collection", "extraction")
    include_assay_conn = any(kw in user_query.lower() for kw in assay_conn_keywords)
    assay_conn_context = ""
    if include_assay_conn and getattr(config, "ASSAY_SAMPLE_CONNECTIONS", None):
        connections = config.ASSAY_SAMPLE_CONNECTIONS.get("connections", [])
        if connections:
            assay_conn_context = "ASSAY-SAMPLE CONNECTIONS (assay → parent_type → child_type, use to determine which side a sample type sits on for a given assay):\n" + json.dumps(connections, indent=2)
            print(f"[DEBUG][GRAPH] Including assay-sample connections ({len(connections)} entries)")

    # Use full parser plan when available (contains resolved entities + routing intent + filters)
    # Fall back to raw entity dict when called without a parser plan
    if plan_dict:
        upstream_context = "PARSER PLAN (from Parser Agent — routing intent + resolved entities + filters):\n" + json.dumps(plan_dict, indent=2)
    else:
        entity_json = json.dumps(entity_dict, indent=2)
        upstream_context = "RESOLVED ENTITIES (from Entity Agent — no parser plan available):\n" + entity_json

    messages = [
        {"role": "system", "content": config.GRAPH_AGENT_SYSTEM_PROMPT},
        {"role": "system", "content": "GRAPH SCHEMA (node labels, relationships, properties, vocabulary):\n" + schema_json},
        {"role": "system", "content": upstream_context},
    ]
    if protocol_context:
        messages.append({"role": "system", "content": protocol_context})
    if assay_conn_context:
        messages.append({"role": "system", "content": assay_conn_context})
    if retry_context:
        messages.append({"role": "system", "content": retry_context})
    if refine_context:
        messages.append({"role": "system", "content": refine_context})
    messages.append({"role": "user", "content": user_query})

    graph_client, graph_model, graph_budget = config.get_agent_model("graph")
    try:
        result = call_llm_structured(
            config=config,
            prompt=user_query,
            model=GraphAgentPlan,
            system=config.GRAPH_AGENT_SYSTEM_PROMPT,
            messages=messages,
            model_name=graph_model,
            temperature=0,
            response_format={"type": "json_object"},
            log_label="graph_agent",
            thinking_budget=graph_budget,
            client=graph_client,
        )
        print(f"[DEBUG][GRAPH] Generated cypher: {result.cypher!r}")

        # Canonical-UID guard: runs FIRST so everything downstream, including the
        # property guard and the filter guard, sees the canonical spelling.
        result.cypher, uid_notes = canonicalize_sample_uid_property(result.cypher)
        if uid_notes:
            result.explanation = (
                f"{result.explanation} [uid guard: {'; '.join(uid_notes)}]"
            ).strip()
            print(f"[DEBUG][GRAPH] Canonicalised UID property: {result.cypher!r}")

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
            result = call_llm_structured(
                config=config,
                prompt="Regenerate the Cypher.",
                model=GraphAgentPlan,
                system=config.GRAPH_AGENT_SYSTEM_PROMPT,
                messages=messages,
                model_name=graph_model,
                temperature=0,
                response_format={"type": "json_object"},
                log_label="graph_agent_repair",
                thinking_budget=graph_budget,
                client=graph_client,
            )
            print(f"[DEBUG][GRAPH] Repaired cypher: {result.cypher!r}")
            still = unknown_cypher_properties(result.cypher, known)
            if still:
                print(f"[DEBUG][GRAPH] Repair still references unknown properties: {still}; returning empty plan")
                return GraphAgentPlan(
                    cypher="",
                    explanation=f"Graph agent could not produce valid Cypher; properties "
                                f"{still} do not exist on any node in the schema.",
                    parameters={},
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
        return result
    except Exception as e:
        print(f"[DEBUG][GRAPH] graph_agent failed: {e!r}")
        return GraphAgentPlan(cypher="", explanation=f"Graph agent error: {e}", parameters={})

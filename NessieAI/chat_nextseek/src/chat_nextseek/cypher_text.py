"""
Cypher text helpers shared by the graph agent's guards and the Neo4j tool.

`mask_cypher` blanks string literals, backticked names, `$params` and comments with
spaces, so every offset still lines up with the original text: clause scanning runs
on the mask, slicing on the original. It moved here from `agents/graph.py`, which
keeps `_mask_cypher` as an alias.

`write_clause` is the text check in front of `helpers/tools/neo4j.py::tool_neo4j_query`
(spec D3). It runs on masked text, so a literal such as 'Data Set', a backticked
property such as `SET_ID` or a comment never trips it, and it refuses every procedure
except `db.index.fulltext.queryNodes`, the read-only fulltext search graph_search uses.
`CALL { }` and `CALL (x) { }` subqueries are allowed; their bodies are scanned like
the rest of the text. The check is the first line only: the tool also runs every
statement in a READ transaction, so the server refuses a write the check misses.

`extra_procedures` widens the allowlist for one call and nothing else: an evaluation
prompt variant's `allowed_procedures` (`prompt_variants.py`), which the variant's
config copy carries as `EXTRA_ALLOWED_PROCEDURES`. `ALLOWED_PROCEDURES` itself never
changes, so a turn without the variant refuses exactly what it always did.

An allowed `apoc.path.*` procedure is allowed only in a shape that cannot run away
(`_apoc_path_reasons`), because nothing on the server bounds it. Measured on the local
1.2 graph on 2026-09-17: APOC's path expansion is NOT charged to the transaction memory
cap. `apoc.path.expandConfig` from one sample with no relationshipFilter and the default
RELATIONSHIP_PATH uniqueness at maxLevel 4 ran the Neo4j JVM out of heap
(`java.lang.OutOfMemoryError`) and left the server refusing every connection until it
was restarted; the 1g transaction cap and the 120 s timeout stopped nothing. With no
relationshipFilter, `subgraphNodes` from one sample reached 1,088,423 nodes by level 4
through the OF_TYPE and IN_PROJECT hubs. So every allowed `apoc.path.*` call must carry
a literal configuration map with a literal integer `maxLevel` from 1 to
`APOC_PATH_MAX_LEVEL`, a literal `relationshipFilter` naming only DERIVED_FROM, no
`sequence` (it replaces the filter), `uniqueness: 'NODE_GLOBAL'` on `expandConfig` and
no other uniqueness anywhere; the positional `apoc.path.expand` cannot set uniqueness
and is refused. `procedure_call_problems` names each problem for the graph agent's one
repair; `write_clause` refuses the call when it reaches the tool anyway.
"""
from __future__ import annotations

import re

ALLOWED_PROCEDURES = frozenset({"db.index.fulltext.queryNodes"})

#: The largest literal maxLevel an apoc.path call may carry. Measured on the 1.2 graph (2026-09-17): the longest
#: DERIVED_FROM chain is 11 hops (3 samples start one, none starts a 12-hop chain; every chain past 8 hops is a
#: D.IMG over a run of CHM), so 12 reaches every ancestor and every descendant.
APOC_PATH_MAX_LEVEL = 12
APOC_PATH_PREFIX = "apoc.path."
#: The one relationship an apoc.path call may walk. A hub relationship (OF_TYPE, IN_PROJECT, IN_STUDY) reaches most of
#: the graph in two hops.
APOC_PATH_RELATIONSHIP = "DERIVED_FROM"
_NODE_GLOBAL = "NODE_GLOBAL"
#: apoc.path procedures whose default uniqueness (RELATIONSHIP_PATH) enumerates every path, so the call must ask for
#: NODE_GLOBAL; subgraphNodes, subgraphAll and spanningTree fix NODE_GLOBAL themselves.
_UNIQUENESS_REQUIRED = frozenset({"apoc.path.expandConfig"})
#: apoc.path procedures that take positional arguments and cannot set uniqueness at all.
_POSITIONAL = frozenset({"apoc.path.expand"})


def mask_cypher(text: str) -> str:
    """
    Blank out string literals, backtick identifiers, `$params` and comments,
    replacing each character with a space so every offset still lines up with the
    original text. Clause scanning runs on the mask; slicing runs on the original.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch:
                    break
                j += 1
            end = min(j + 1, n)
        elif ch == "$":
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            end = j
        elif text.startswith("//", i):
            j = text.find("\n", i)
            end = n if j == -1 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            end = n if j == -1 else j + 2
        else:
            i += 1
            continue
        for k in range(i, end):
            out[k] = " "
        i = max(end, i + 1)
    return "".join(out)


# Write clauses, administration commands, and CALL (checked separately). Longer forms
# come first so `DETACH DELETE` wins over `DELETE` at the same offset. `INSERT` is the
# GQL synonym of CREATE; `IN ... TRANSACTIONS` batches writes and cannot run inside a
# READ transaction anyway.
_CLAUSE_RE = re.compile(
    r"\b(?:"
    r"(?P<detach>DETACH\s+DELETE)"
    r"|(?P<load>LOAD\s+CSV)"
    r"|(?P<batch>IN\s+(?:[A-Za-z0-9_]+\s+)?(?:CONCURRENT\s+)?TRANSACTIONS)"
    r"|(?P<db_admin>(?:START|STOP)\s+DATABASE)"
    r"|(?P<word>CREATE|INSERT|MERGE|SET|DELETE|REMOVE|DROP|FOREACH|SHOW|USE|GRANT|DENY"
    r"|REVOKE|ALTER|RENAME|TERMINATE)"
    r"|(?P<call>CALL)"
    r")\b",
    re.IGNORECASE,
)

_PROCEDURE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _is_name_not_clause(masked: str, start: int, end: int) -> bool:
    """A keyword-shaped word that is a property, a label, a relationship type or a map key."""
    before = masked[start - 1] if start > 0 else ""
    if before in (".", ":"):
        return True
    after = masked[end:].lstrip(" \t\r\n")
    return after.startswith(":")


def _skip_space(masked: str, original: str, i: int) -> int:
    """Skip blanks in the mask, stopping on a backtick (a masked name, not space)."""
    n = len(masked)
    while i < n and masked[i].isspace() and original[i] != "`":
        i += 1
    return i


def _matching_paren(masked: str, i: int) -> int:
    """Index of the `)` closing the `(` at `i`, or -1."""
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == "(":
            depth += 1
        elif masked[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _call_problem(masked: str, original: str, end: int,
                  allowed: frozenset[str] = ALLOWED_PROCEDURES) -> str | None:
    """None for a subquery or an allowed procedure; else the refused clause."""
    i = _skip_space(masked, original, end)
    if i >= len(masked):
        return "CALL"
    if original[i] == "`":
        return "CALL with a backticked procedure name"
    if masked[i] == "{":
        return None
    if masked[i] == "(":
        close = _matching_paren(masked, i)
        if close != -1:
            k = _skip_space(masked, original, close + 1)
            if k < len(masked) and masked[k] == "{":
                return None
        return "CALL"
    m = _PROCEDURE_NAME_RE.match(original, i)
    if not m:
        return "CALL"
    name = m.group(0)
    k = _skip_space(masked, original, m.end())
    follows = original[k] if k < len(original) else ""
    if name in allowed and follows not in (".", "`"):
        if name.startswith(APOC_PATH_PREFIX):
            reasons = _apoc_path_reasons(name, masked, original, k)
            if reasons:
                return f"CALL {name} ({'; '.join(reasons)})"
        return None
    if follows == "`":
        return "CALL with a backticked procedure name"
    return f"CALL {name.rstrip('.')}"


# --------------------------------------------------------------------------- #
# The shape of an allowed apoc.path call (see the module docstring for why)
# --------------------------------------------------------------------------- #

_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STRING_LITERAL_RE = re.compile(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", re.DOTALL)
_FILTER_HINT = "'DERIVED_FROM>' walks to ancestors, '<DERIVED_FROM' to descendants, 'DERIVED_FROM' both ways"


def _matching(masked: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at `i`, or -1."""
    depth = 0
    for j in range(i, len(masked)):
        if masked[j] == open_ch:
            depth += 1
        elif masked[j] == close_ch:
            depth -= 1
            if depth == 0:
                return j
    return -1


def _top_level_items(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """The comma-separated spans of `masked[start:end]` at bracket depth 0."""
    spans, depth, item_start = [], 0, start
    for i in range(start, end):
        ch = masked[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((item_start, i))
            item_start = i + 1
    spans.append((item_start, end))
    return spans


def _strip_span(masked: str, original: str, s: int, e: int) -> tuple[int, int]:
    """Trim blanks off a span; a masked backtick name, parameter or string is not blank in the original."""
    while s < e and masked[s].isspace() and original[s].isspace():
        s += 1
    while e > s and masked[e - 1].isspace() and original[e - 1].isspace():
        e -= 1
    return s, e


def _read_key(masked: str, original: str, s: int, e: int) -> tuple[str | None, int]:
    """The map key at `s` (plain, or backticked with `` for a literal backtick) and the index after its colon."""
    if s < e and original[s] == "`":
        out, j = [], s + 1
        while j < e:
            if original[j] == "`":
                if j + 1 < e and original[j + 1] == "`":
                    out.append("`")
                    j += 2
                    continue
                break
            out.append(original[j])
            j += 1
        key, j = "".join(out), j + 1
    else:
        m = _KEY_RE.match(masked, s, e)
        if not m:
            return None, s
        key, j = m.group(0), m.end()
    while j < e and masked[j].isspace():
        j += 1
    if j >= e or masked[j] != ":":
        return None, s
    return key, j + 1


def _string_value(original: str) -> str | None:
    """The content of `original` when it is exactly one string literal, else None."""
    m = _STRING_LITERAL_RE.fullmatch(original)
    if not m:
        return None
    raw = m.group(1) if m.group(1) is not None else m.group(2)
    return re.sub(r"\\(.)", r"\1", raw)


def _apoc_path_reasons(name: str, masked: str, original: str, k: int) -> list[str]:
    """Why an allowed apoc.path call at `original[k]` (just past its name) may not run; [] when it may."""
    if name in _POSITIONAL:
        return ["its uniqueness cannot be set; use apoc.path.subgraphNodes, apoc.path.spanningTree, or "
                "apoc.path.expandConfig with uniqueness: 'NODE_GLOBAL'"]
    if k >= len(masked) or masked[k] != "(":
        return ["pass the start node and a literal configuration map in parentheses"]
    close = _matching(masked, k, "(", ")")
    if close == -1:
        return ["pass the start node and a literal configuration map in parentheses"]
    args = _top_level_items(masked, k + 1, close)
    literal_map = "the configuration must be a literal configuration map {...} written in the query"
    if len(args) < 2:
        return [literal_map]
    s, e = _strip_span(masked, original, *args[1])
    if s >= e or masked[s] != "{" or _matching(masked, s, "{", "}") != e - 1:
        return [literal_map]
    entries: dict[str, list[tuple[str, str]]] = {}
    for item_s, item_e in _top_level_items(masked, s + 1, e - 1):
        item_s = _skip_space(masked, original, item_s)  # blanks and comments, never a backticked key
        if item_s >= item_e:
            continue
        key, value_start = _read_key(masked, original, item_s, item_e)
        if key is None:
            return [literal_map]
        vs, ve = _strip_span(masked, original, value_start, item_e)
        entries.setdefault(key, []).append((masked[vs:ve], original[vs:ve]))

    reasons: list[str] = []
    bound = f"a literal integer from 1 to {APOC_PATH_MAX_LEVEL}"
    levels = entries.get("maxLevel")
    if not levels:
        reasons.append(f"no maxLevel; give {bound} (the longest DERIVED_FROM chain is 11 hops)")
    for masked_value, original_value in levels or ():
        if not re.fullmatch(r"[0-9]+", masked_value.strip()):
            reasons.append(f"maxLevel must be {bound}, not {original_value.strip() or 'empty'}")
        elif not 1 <= int(masked_value) <= APOC_PATH_MAX_LEVEL:
            reasons.append(f"maxLevel {int(masked_value)} is outside 1 to {APOC_PATH_MAX_LEVEL}")

    filters = entries.get("relationshipFilter")
    if not filters:
        reasons.append(f"no relationshipFilter; walk DERIVED_FROM only: {_FILTER_HINT}")
    for _, original_value in filters or ():
        content = _string_value(original_value)
        if content is None:
            reasons.append(f"relationshipFilter must be a literal string: {_FILTER_HINT}")
            continue
        tokens = [t.strip() for t in re.split(r"[|,]", content)]
        if not all(t.lstrip("<").rstrip(">").strip() == APOC_PATH_RELATIONSHIP for t in tokens):
            reasons.append(f"relationshipFilter '{content}' must name only DERIVED_FROM: {_FILTER_HINT}")

    if "sequence" in entries:
        reasons.append("sequence replaces the relationshipFilter and is not allowed; use relationshipFilter and "
                       "labelFilter")
    uniqueness = entries.get("uniqueness")
    if name in _UNIQUENESS_REQUIRED and not uniqueness:
        reasons.append("no uniqueness; set uniqueness: 'NODE_GLOBAL' (the default, RELATIONSHIP_PATH, walks every "
                       "path to every node)")
    for _, original_value in uniqueness or ():
        if _string_value(original_value) != _NODE_GLOBAL:
            reasons.append(f"uniqueness {original_value.strip()} is not 'NODE_GLOBAL'")
    return reasons


def _named_call(masked: str, original: str, end: int) -> tuple[str, int] | None:
    """(procedure name, index just past it) for the CALL keyword ending at `end`, or None for anything else."""
    i = _skip_space(masked, original, end)
    if i >= len(masked) or original[i] == "`" or masked[i] in "({":
        return None
    m = _PROCEDURE_NAME_RE.match(original, i)
    if not m:
        return None
    return m.group(0), _skip_space(masked, original, m.end())


def procedure_call_problems(text: str | None, extra_procedures=()) -> list[str]:
    """Every procedure call in `text` that the tool would refuse, as a repair can act on it: ``["CALL x: why"]``.

    A call the allowlist (``ALLOWED_PROCEDURES`` plus `extra_procedures`) does not hold is named with the list of
    what may be called; an allowed ``apoc.path.*`` call is named once per broken rule. Subqueries, and write clauses
    (``write_clause`` refuses those at the tool), are not problems here. The graph agent calls this only for a turn
    whose variant allows procedures, so the default path is unchanged.
    """
    if not text:
        return []
    allowed = ALLOWED_PROCEDURES | _extra(extra_procedures)
    masked = mask_cypher(text)
    out: list[str] = []
    for m in _CLAUSE_RE.finditer(masked):
        if not m.group("call") or _is_name_not_clause(masked, m.start(), m.end()):
            continue
        if _call_problem(masked, text, m.end(), allowed) is None:
            continue
        named = _named_call(masked, text, m.end())
        if named and named[0] in allowed and named[0].startswith(APOC_PATH_PREFIX):
            out += [f"CALL {named[0]}: {reason}" for reason in _apoc_path_reasons(named[0], masked, text, named[1])]
        else:
            what = f"CALL {named[0].rstrip('.')}" if named else _call_problem(masked, text, m.end(), allowed)
            out.append(f"{what}: not a procedure you may call here; the procedures you may call are "
                       f"{', '.join(sorted(allowed))}")
    return list(dict.fromkeys(out))


def _extra(extra_procedures) -> frozenset[str]:
    """Names from a real collection of strings; anything else (a bare string, a mock's attribute) adds none."""
    if not isinstance(extra_procedures, (set, frozenset, tuple, list)):
        return frozenset()
    return frozenset(p for p in extra_procedures if isinstance(p, str))


def variant_procedures(config) -> frozenset[str]:
    """The procedures an evaluation prompt variant allows on this config copy (``EXTRA_ALLOWED_PROCEDURES``), or none.

    Type-checked, so a MagicMock config (whose every attribute exists) allows nothing.
    """
    return _extra(getattr(config, "EXTRA_ALLOWED_PROCEDURES", ()))


def write_clause(text: str | None, extra_procedures=()) -> str | None:
    """
    The first clause that makes `text` more than a read, or None.

    Masks the text first (masking masked text changes nothing), then refuses write
    clauses, administration commands, `LOAD CSV`, `FOREACH`, `USE`, batched
    `IN TRANSACTIONS`, and every procedure call except `db.index.fulltext.queryNodes`
    and any named in `extra_procedures`, including one whose name is backticked. A
    `CALL { }` or `CALL (x) { }` subquery is allowed, and its body is checked like the rest.
    """
    if not text:
        return None
    allowed = ALLOWED_PROCEDURES | _extra(extra_procedures)
    masked = mask_cypher(text)
    for m in _CLAUSE_RE.finditer(masked):
        if _is_name_not_clause(masked, m.start(), m.end()):
            continue
        if m.group("call"):
            problem = _call_problem(masked, text, m.end(), allowed)
            if problem is None:
                continue
            return problem
        if m.group("batch"):
            return "IN TRANSACTIONS"
        return " ".join(m.group(0).upper().split())
    return None

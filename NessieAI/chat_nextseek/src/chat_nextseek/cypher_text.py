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
"""
from __future__ import annotations

import re

ALLOWED_PROCEDURES = frozenset({"db.index.fulltext.queryNodes"})


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
        return None
    if follows == "`":
        return "CALL with a backticked procedure name"
    return f"CALL {name.rstrip('.')}"


def _extra(extra_procedures) -> frozenset[str]:
    """Names from a real collection of strings; anything else (a bare string, a mock's attribute) adds none."""
    if not isinstance(extra_procedures, (set, frozenset, tuple, list)):
        return frozenset()
    return frozenset(p for p in extra_procedures if isinstance(p, str))


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

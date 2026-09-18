"""
The project scope on the graph agent's Cypher: inject it, and prove in code that every node a statement can touch is
scoped. What cannot be proven is refused.

For a caller who is not an admin, ``scope_cypher`` reads the statement with its own Cypher lexer and a recursive-descent
recognizer for one fixed grammar (spec section 5.3), classifies every node and relationship the statement binds
(section 5.4), and returns the statement with the scope inserted (section 5.6), or a refusal naming every reason
(section 5.8). It never raises: an internal error is itself a refusal.

- A Sample-capable node (every label ``Sample`` or ``T_*``, or no label as an end of ``DERIVED_FROM``) gets
  graph_search's clause, ``any(p IN s.project_ids WHERE p IN $projects)``, rendered with generated names.
- A ``Project`` gets ``p.id IN $__scope_projects``.
- A ``Study``, ``Investigation`` or ``Person`` carries no ``project_ids``; it must be joined, by a relationship
  pattern in the same pattern list, as the container of something visible: the study of a scoped sample, the
  investigation of such a study or of the caller's project, a member of the caller's project (``prove_joined``).
  This is narrower than a same-component rule, which would also admit a study reached down from its investigation
  (a sibling of a visible sample's study, possibly holding only samples the caller cannot see).
- Every node on a variable-length ``DERIVED_FROM`` path is scoped by one clause over ``nodes(path)``, so lineage stops
  at the caller's project edge.
- The sample fulltext search is scoped in its ``YIELD``'s ``WHERE``.
- Catalog nodes (``SampleType``, ``Attribute``), ``GraphMeta``, ``OrphanSample``, unknown labels, untyped or other
  relationships, subqueries other than ``EXISTS``/``COUNT``, procedures, pattern expressions, path selectors, the hidden
  sample properties, dynamic property access and any function outside the allowlist are refused.

The output is the input plus insertions only: deleting what was inserted gives the input back byte for byte. Every
generated name starts with ``__scope``, which is why that prefix is reserved in the statement and its parameters.

The module is pure: no driver, no Django, no import of the agents, the config or the API side. The tool
(``helpers/tools/neo4j.py``) calls it after ``write_clause``; writes never reach it.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 5.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .graph_scope import HIDDEN_SAMPLE_PROPERTIES, RESERVED_PREFIX, SCOPE_PARAM, GraphScope

SCOPE_CLAUSE_TEMPLATE = "any({element} IN {var}.project_ids WHERE {element} IN ${param})"
PROJECT_CLAUSE_TEMPLATE = "{var}.id IN ${param}"
PATH_CLAUSE_TEMPLATE = ("all({node} IN nodes({path}) WHERE "
                        "any({element} IN {node}.project_ids WHERE {element} IN ${param}))")
MAX_CYPHER_CHARS = 20_000
MAX_NESTING = 32

REFUSAL_CODES = (
    "too_long", "too_deep", "lexer", "syntax", "internal", "reserved_name", "reserved_parameter", "query_prefix",
    "union", "call_subquery", "collect_subquery", "procedure", "fulltext_form", "pattern_expression", "inline_where",
    "label_not_allowed", "label_expression", "unlabelled_node", "unjoined_node", "relationship_type",
    "variable_length", "path_selector", "hidden_property", "dynamic_property", "whole_properties",
    "function_not_allowed",
)

# The node and relationship tables of spec section 5.4.
SAMPLE_LABEL = "Sample"
SAMPLE_TYPE_LABEL_PREFIX = "T_"
PROJECT_LABEL = "Project"
JOINED_LABELS = frozenset({"Study", "Investigation", "Person"})
LINEAGE_RELATIONSHIP = "DERIVED_FROM"
FIXED_RELATIONSHIPS = frozenset({"IN_STUDY", "IN_INVESTIGATION", "IN_PROJECT", "MEMBER_OF"})
FULLTEXT_PROCEDURE = "db.index.fulltext.queryNodes"
FULLTEXT_INDEX = "sample_search_text"

# Functions a statement may call (case-insensitive): none of them fetches or walks the graph, so every node or
# relationship value in a row came from a pattern the prover scoped (spec section 5.4, "the invariant").
FUNCTION_ALLOWLIST = frozenset({
    # aggregates
    "avg", "collect", "count", "max", "min", "percentilecont", "percentiledisc", "stdev", "stdevp", "sum",
    # scalar and list
    "coalesce", "elementid", "endnode", "head", "id", "isempty", "isnan", "keys", "labels", "last", "length",
    "nodes", "nullif", "range", "relationships", "reverse", "size", "startnode", "tail", "type", "valuetype",
    # conversions
    "toboolean", "tobooleanornull", "tobooleanlist", "tofloat", "tofloatornull", "tofloatlist", "tointeger",
    "tointegerornull", "tointegerlist", "tostring", "tostringornull", "tostringlist",
    # math
    "abs", "ceil", "floor", "rand", "round", "sign", "e", "exp", "log", "log10", "sqrt", "acos", "asin", "atan",
    "atan2", "cos", "cosh", "cot", "coth", "degrees", "haversin", "pi", "radians", "sin", "sinh", "tan", "tanh",
    # strings
    "left", "right", "ltrim", "rtrim", "trim", "btrim", "lower", "upper", "tolower", "toupper", "replace", "split",
    "substring", "normalize", "char_length", "character_length",
    # temporal constructors
    "date", "datetime", "localdatetime", "localtime", "time", "duration",
})
FUNCTION_NAMESPACE_ALLOWLIST = frozenset({
    "date.truncate", "date.realtime", "date.statement", "date.transaction",
    "datetime.truncate", "datetime.fromepoch", "datetime.fromepochmillis", "datetime.realtime",
    "datetime.statement", "datetime.transaction",
    "localdatetime.truncate", "localdatetime.realtime", "localdatetime.statement", "localdatetime.transaction",
    "localtime.truncate", "localtime.realtime", "localtime.statement", "localtime.transaction",
    "time.truncate", "time.realtime", "time.statement", "time.transaction",
    "duration.between", "duration.inmonths", "duration.indays", "duration.inseconds",
})
# APOC function families that only compute over their arguments (when APOC is loaded at all).
PURE_APOC_FAMILIES = ("apoc.text.", "apoc.coll.", "apoc.number.", "apoc.math.", "apoc.date.", "apoc.temporal.")

_HIDDEN = frozenset(name.lower() for name in HIDDEN_SAMPLE_PROPERTIES)


@dataclass(frozen=True)
class Scoped:
    cypher: str                        # the statement to run: the input plus insertions only
    parameters: dict[str, Any]         # the input's parameters, plus {SCOPE_PARAM: list(project_ids)} for a non-admin
    decision: Literal["admin", "proven"]
    injected: tuple[str, ...] = ()     # one line per predicate added: "s: sample clause", "__scope_path1: every node"
    joined: tuple[str, ...] = ()       # one line per joined node, e.g. "st (Study): joined to s"


@dataclass(frozen=True)
class Refused:
    codes: tuple[str, ...]             # section 5.8, in order found, each once
    reasons: tuple[str, ...]           # one short neutral sentence per finding, with line and column
    decision: Literal["refused"] = "refused"


def scope_cypher(cypher: str, parameters: Mapping[str, Any] | None, scope: GraphScope) -> Scoped | Refused:
    """Pure. Admin: the reserved-parameter check only, then Scoped(cypher unchanged, parameters copied, "admin").
    Non-admin: lex, recognize, inject; any construct outside section 5.3 refuses. Never raises."""
    return _scope_with_insertions(cypher, parameters, scope)[0]


def strip_hidden(value: Any) -> Any:
    """For a non-admin's result rows: every graph Node (anything with labels and element_id and items()) becomes a
    plain dict of its properties minus HIDDEN_SAMPLE_PROPERTIES; a Relationship becomes a dict of its properties;
    a Path becomes {"nodes": [...], "relationships": [...]}; lists, tuples and dicts are walked. Other values pass."""
    if value is None or isinstance(value, (str, bytes, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {key: strip_hidden(item) for key, item in value.items()}
    if isinstance(value, list):
        return [strip_hidden(item) for item in value]
    if isinstance(value, tuple):
        return tuple(strip_hidden(item) for item in value)
    try:
        if hasattr(value, "nodes") and hasattr(value, "relationships") and not hasattr(value, "items"):
            return {"nodes": [strip_hidden(node) for node in value.nodes],
                    "relationships": [strip_hidden(rel) for rel in value.relationships]}
        if hasattr(value, "element_id") and callable(getattr(value, "items", None)):
            if hasattr(value, "labels"):
                return {key: strip_hidden(item) for key, item in value.items() if str(key).lower() not in _HIDDEN}
            return {key: strip_hidden(item) for key, item in value.items()}
    except Exception:
        return None
    return value


# --------------------------------------------------------------------------- #
# The entry point, with the insertions exposed for the property tests
# --------------------------------------------------------------------------- #

def _scope_with_insertions(cypher: Any, parameters: Any, scope: Any) -> tuple[Scoped | Refused, tuple]:
    """(outcome, insertions): the insertions are (offset, text) in the input, in the order they apply."""
    findings = _Findings("")
    try:
        if not isinstance(scope, GraphScope):
            findings.add("internal", 0, "no project scope was given")
            return findings.refused(), ()
        if parameters is None:
            parameters = {}
        if not isinstance(parameters, Mapping):
            findings.add("internal", 0, "the parameters are not a map")
            return findings.refused(), ()
        if not isinstance(cypher, str):
            findings.add("internal", 0, "the statement is not text")
            return findings.refused(), ()
        findings.text = cypher
        for key in parameters:
            if isinstance(key, str) and key.lower().startswith(RESERVED_PREFIX):
                findings.add("reserved_parameter", 0, f"the parameter {key} uses the reserved prefix {RESERVED_PREFIX}")
        if scope.is_admin:
            if findings.codes:
                return findings.refused(), ()
            return Scoped(cypher=cypher, parameters=dict(parameters), decision="admin"), ()
        if len(cypher) > MAX_CYPHER_CHARS:
            findings.add("too_long", 0, f"the statement is longer than {MAX_CYPHER_CHARS} characters")
            return findings.refused(), ()
        try:
            tokens = _lex(cypher)
        except _LexError as err:
            findings.add("lexer", err.offset, err.message)
            return findings.refused(), ()
        for tok in tokens:
            if tok.kind in ("name", "bname", "param") and tok.value.lower().startswith(RESERVED_PREFIX):
                findings.add("reserved_name", tok.start, f"the name {tok.value} uses the reserved prefix "
                                                         f"{RESERVED_PREFIX}")
        parser = _Parser(cypher, tokens, findings)
        try:
            parser.statement()
        except _Stop:
            pass
        if findings.codes:
            return findings.refused(), ()
        insertions = tuple((offset, text) for offset, _, text in sorted(parser.insertions, key=lambda x: x[:2]))
        out = _apply(cypher, insertions)
        return Scoped(
            cypher=out,
            parameters={**dict(parameters), SCOPE_PARAM: list(scope.project_ids)},
            decision="proven",
            injected=tuple(line for _, line in sorted(parser.injected, key=lambda x: x[0])),
            joined=tuple(line for _, line in sorted(parser.joined, key=lambda x: x[0])),
        ), insertions
    except Exception:
        findings.add("internal", 0, "the prover failed on this statement")
        return findings.refused(), ()


def _apply(text: str, insertions: tuple[tuple[int, str], ...]) -> str:
    out, last = [], 0
    for offset, piece in insertions:
        out.append(text[last:offset])
        out.append(piece)
        last = offset
    out.append(text[last:])
    return "".join(out)


class _Findings:
    """Every refusal found, in order: codes once each, one reason per finding."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.codes: list[str] = []
        self.reasons: list[str] = []

    def add(self, code: str, offset: int, message: str) -> None:
        if code not in self.codes:
            self.codes.append(code)
        text = self.text or ""
        offset = max(0, min(offset, len(text)))
        line = text.count("\n", 0, offset) + 1
        column = offset - (text.rfind("\n", 0, offset) + 1) + 1
        self.reasons.append(f"line {line}, column {column}: {message}")

    def refused(self) -> Refused:
        return Refused(codes=tuple(self.codes), reasons=tuple(self.reasons))


# --------------------------------------------------------------------------- #
# The lexer: Cypher's rules, and nothing it does not recognize
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class _Tok:
    kind: str    # "name", "bname" (backticked), "string", "param", "number", "punct", "eof"
    value: str   # a name (unescaped for bname), a string's raw body, a parameter's name, a number, a symbol
    start: int
    end: int


class _LexError(Exception):
    def __init__(self, offset: int, message: str) -> None:
        super().__init__(message)
        self.offset = offset
        self.message = message


_SPACE = frozenset(" \t\n\r")
_PUNCT2 = ("<>", "<=", ">=", "=~", "!=", "..", "||")
_PUNCT1 = frozenset("()[]{},.:;=<>+-*/%^|&!$")


def _name_char(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch == "_")


def _name_start(ch: str) -> bool:
    return ch.isascii() and (ch.isalpha() or ch == "_")


def _digit(ch: str) -> bool:
    return ch.isascii() and ch.isdigit()


def _number_end(text: str, i: int) -> int:
    n = len(text)
    if text.startswith(("0x", "0X"), i):
        j = i + 2
        while j < n and text[j] in "0123456789abcdefABCDEF":
            j += 1
        return j
    if text.startswith(("0o", "0O"), i):
        j = i + 2
        while j < n and text[j] in "01234567":
            j += 1
        return j
    j = i
    while j < n and _digit(text[j]):
        j += 1
    if j + 1 < n and text[j] == "." and _digit(text[j + 1]):
        j += 1
        while j < n and _digit(text[j]):
            j += 1
    if j < n and text[j] in "eE":
        k = j + 1
        if k < n and text[k] in "+-":
            k += 1
        if k < n and _digit(text[k]):
            j = k
            while j < n and _digit(text[j]):
                j += 1
    return j


def _lex(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in _SPACE:
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j == -1:
                raise _LexError(i, "an unterminated comment")
            i = j + 2
            continue
        if ch in ("'", '"'):
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            if j >= n:
                raise _LexError(i, "an unterminated string")
            toks.append(_Tok("string", text[i + 1:j], i, j + 1))
            i = j + 1
            continue
        if ch == "`":
            j, parts = i + 1, []
            while True:
                if j >= n:
                    raise _LexError(i, "an unterminated backticked name")
                c = text[j]
                if c == "\\":
                    raise _LexError(j, "a backslash inside a backticked name")
                if c == "`":
                    if j + 1 < n and text[j + 1] == "`":
                        parts.append("`")
                        j += 2
                        continue
                    break
                parts.append(c)
                j += 1
            if not parts:
                raise _LexError(i, "an empty backticked name")
            toks.append(_Tok("bname", "".join(parts), i, j + 1))
            i = j + 1
            continue
        if ch == "$":
            j = i + 1
            if j < n and text[j] == "`":
                raise _LexError(i, "a backticked parameter")
            if j < n and (_name_start(text[j]) or _digit(text[j])):
                k = j
                if _digit(text[j]):
                    while k < n and _digit(text[k]):
                        k += 1
                else:
                    while k < n and _name_char(text[k]):
                        k += 1
                if k < n and _name_char(text[k]):
                    raise _LexError(k, "a parameter name that mixes digits and letters")
                toks.append(_Tok("param", text[j:k], i, k))
                i = k
                continue
            toks.append(_Tok("punct", "$", i, i + 1))
            i += 1
            continue
        if _digit(ch):
            j = _number_end(text, i)
            if j < n and _name_char(text[j]):
                raise _LexError(j, "a name that starts with a digit")
            toks.append(_Tok("number", text[i:j], i, j))
            i = j
            continue
        if _name_start(ch):
            j = i + 1
            while j < n and _name_char(text[j]):
                j += 1
            toks.append(_Tok("name", text[i:j], i, j))
            i = j
            continue
        two = text[i:i + 2]
        if two in _PUNCT2:
            toks.append(_Tok("punct", two, i, i + 2))
            i += 2
            continue
        if ch in _PUNCT1:
            toks.append(_Tok("punct", ch, i, i + 1))
            i += 1
            continue
        raise _LexError(i, "a character the prover does not read")
    toks.append(_Tok("eof", "", n, n))
    return toks


# --------------------------------------------------------------------------- #
# The recognizer
# --------------------------------------------------------------------------- #

class _Stop(Exception):
    """The parse cannot go on; the findings so far stand."""


# Words that are never a variable name here (keywords of the grammar and of the clauses it refuses).
_RESERVED = frozenset("""
AND AS ASC ASCENDING BY CALL CASE CONTAINS CREATE DELETE DESC DESCENDING DETACH DISTINCT ELSE END ENDS EXISTS FALSE
FILTER FINISH FOREACH IN INSERT IS LET LIMIT LOAD MATCH MERGE NEXT NOT NULL OFFSET OPTIONAL OR ORDER REMOVE RETURN SET
SKIP STARTS THEN TRUE UNION UNWIND USE WHEN WHERE WITH XOR YIELD
""".split())
_CLAUSE_START = frozenset({"MATCH", "OPTIONAL", "WITH", "UNWIND", "CALL", "RETURN"})
_SKIP_STOP = frozenset({"MATCH", "OPTIONAL", "WITH", "UNWIND", "CALL", "RETURN", "ORDER", "SKIP", "LIMIT", "UNION",
                        "YIELD"})
_COMPARISON = frozenset({"=", "<>", "!=", "<", ">", "<=", ">=", "=~"})
_OPENERS = {"(": ")", "[": "]", "{": "}"}


@dataclass(frozen=True)
class _Names:
    """Names in scope. `strict` holds what a pattern may reference; `loose` adds names an expression alone may use
    (the previous clause's names in an ORDER BY). A pattern naming a loose-only name is refused."""

    strict: frozenset = frozenset()
    loose: frozenset = frozenset()

    def bind(self, names) -> "_Names":
        names = frozenset(names)
        return _Names(self.strict | names, self.loose | names)


@dataclass
class _Node:
    open_off: int
    var: str | None = None
    var_raw: str | None = None
    labels: list[str] = field(default_factory=list)
    refused: bool = False
    key: Any = None


@dataclass
class _Rel:
    start_off: int
    rtype: str | None
    varlen: bool
    refused: bool
    var: str | None = None


@dataclass
class _Path:
    start_off: int
    node_start_off: int
    name: str | None = None
    name_raw: str | None = None
    nodes: list[_Node] = field(default_factory=list)
    rels: list[_Rel] = field(default_factory=list)
    opaque: bool = False
    varlen: bool = False               # holds a variable-length DERIVED_FROM
    mixed: bool = False                # ... beside another relationship type


@dataclass
class _PatternList:
    paths: list[_Path]
    end_off: int
    bound: set[str]


@dataclass
class _Vertex:
    key: Any
    first: _Node
    var: str | None
    var_raw: str | None
    labels: set[str] = field(default_factory=set)
    refused: bool = False
    reference: bool = False
    derived_end: bool = False
    in_varlen: bool = False
    kind: str = ""                     # "sample", "project", "joined", "reference", "none"
    ref_text: str | None = None        # how an injected clause names it


def _label_kind(label: str) -> str:
    if label == SAMPLE_LABEL or (label.startswith(SAMPLE_TYPE_LABEL_PREFIX)
                                 and len(label) > len(SAMPLE_TYPE_LABEL_PREFIX)):
        return "sample"
    if label == PROJECT_LABEL:
        return "project"
    if label in JOINED_LABELS:
        return "joined"
    return "none"


class _Parser:
    def __init__(self, text: str, toks: list[_Tok], findings: _Findings) -> None:
        self.text = text
        self.toks = toks
        self.i = 0
        self.f = findings
        self.depth = 0
        self.seq = 0.0
        self.insertions: list[tuple[int, float, str]] = []
        self.injected: list[tuple[int, str]] = []
        self.joined: list[tuple[int, str]] = []
        self.counters = {"p": 0, "m": 0, "n": 0, "path": 0}
        self.paren_closes: set[int] = set()
        self.anon = 0

    # ------------------------------------------------------------------ token helpers
    @property
    def tok(self) -> _Tok:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> _Tok:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    def at_p(self, sym: str, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind == "punct" and t.value == sym

    def at_kw(self, word: str, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind == "name" and t.value.upper() == word

    def at_name(self, k: int = 0) -> bool:
        t = self.peek(k) if k else self.tok
        return t.kind in ("name", "bname")

    def raw(self, t: _Tok) -> str:
        return self.text[t.start:t.end]

    def syntax(self, message: str | None = None) -> None:
        t = self.tok
        if message is None:
            message = "the statement ends too early" if t.kind == "eof" else \
                f"unexpected {self.raw(t)[:24]!r}"
        self.f.add("syntax", t.start, message)
        raise _Stop

    def refuse(self, code: str, offset: int, message: str) -> None:
        self.f.add(code, offset, message)

    def expect_p(self, sym: str) -> _Tok:
        if not self.at_p(sym):
            self.syntax()
        t = self.tok
        self.i += 1
        return t

    def expect_kw(self, word: str) -> None:
        if not self.at_kw(word):
            self.syntax()
        self.i += 1

    def binding(self) -> tuple[str, str]:
        """The variable name at the cursor (plain or backticked), consumed."""
        t = self.tok
        if t.kind == "bname" or (t.kind == "name" and t.value.upper() not in _RESERVED):
            self.i += 1
            return t.value, self.raw(t)
        self.syntax()
        raise _Stop  # unreachable

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_NESTING:
            self.refuse("too_deep", self.tok.start, f"nesting deeper than {MAX_NESTING} levels")
            raise _Stop

    def leave(self) -> None:
        self.depth -= 1

    def insert(self, offset: int, text: str, seq: float | None = None) -> None:
        if seq is None:
            self.seq += 1
            seq = self.seq
        self.insertions.append((offset, seq, text))

    def gen(self, kind: str) -> str:
        self.counters[kind] += 1
        return f"{RESERVED_PREFIX}_{kind}{self.counters[kind]}"

    def skip_balanced(self) -> None:
        """Skip the bracketed group opening at the cursor."""
        stack: list[str] = []
        while True:
            t = self.tok
            if t.kind == "eof":
                self.syntax()
            if t.kind == "punct" and t.value in _OPENERS:
                stack.append(_OPENERS[t.value])
            elif t.kind == "punct" and t.value in (")", "]", "}"):
                if not stack or stack.pop() != t.value:
                    self.syntax()
                if not stack:
                    self.i += 1
                    return
            self.i += 1

    def skip_to_enclosing_close(self) -> None:
        """After a refused construct inside an expression: skip to its enclosing closer, a comma or a clause word."""
        depth = 0
        while self.tok.kind != "eof":
            t = self.tok
            if t.kind == "punct" and t.value in _OPENERS:
                depth += 1
            elif t.kind == "punct" and t.value in (")", "]", "}"):
                if depth == 0:
                    return
                depth -= 1
            elif depth == 0 and ((t.kind == "punct" and t.value == ",")
                                 or (t.kind == "name" and t.value.upper() in _SKIP_STOP)):
                return
            self.i += 1

    # ------------------------------------------------------------------ statement and clauses
    def statement(self) -> None:
        while self.at_kw("EXPLAIN") or self.at_kw("PROFILE") or self.at_kw("CYPHER"):
            self.refuse("query_prefix", self.tok.start, "a query prefix (CYPHER, EXPLAIN or PROFILE) is not allowed")
            word = self.tok.value.upper()
            self.i += 1
            if word == "CYPHER":
                resume = _CLAUSE_START | {"EXPLAIN", "PROFILE"}
                while self.tok.kind != "eof" and not (self.tok.kind == "name" and self.tok.value.upper() in resume):
                    self.i += 1
        self.part()
        while self.at_kw("UNION"):
            self.refuse("union", self.tok.start, "UNION joins a second statement")
            self.i += 1
            if self.at_kw("ALL") or self.at_kw("DISTINCT"):
                self.i += 1
            self.part()
        if self.at_p(";"):
            self.i += 1
        if self.tok.kind != "eof":
            self.syntax()

    def part(self) -> None:
        names = _Names()
        while True:
            if self.at_kw("MATCH") or (self.at_kw("OPTIONAL") and self.at_kw("MATCH", 1)):
                names = self.match_clause(names)
            elif self.at_kw("CALL"):
                names = self.call_clause(names)
            elif self.at_kw("UNWIND"):
                self.i += 1
                self.expr(names)
                self.expect_kw("AS")
                name, _ = self.binding()
                names = names.bind({name})
            elif self.at_kw("WITH"):
                names = self.with_clause(names)
            elif self.at_kw("RETURN"):
                self.return_clause(names)
                return
            elif self.tok.kind == "eof":
                self.syntax("the statement must end with RETURN")
            else:
                self.syntax()

    def match_clause(self, names: _Names) -> _Names:
        if self.at_kw("OPTIONAL"):
            self.i += 1
        self.expect_kw("MATCH")
        if self.at_kw("REPEATABLE") or self.at_kw("DIFFERENT"):
            self.refuse("path_selector", self.tok.start, "a match mode is not allowed")
            self.i += 1
            if self.at_name():
                self.i += 1
        plist = self.pattern_list(names)
        inner = names.bind(plist.bound)
        where = self.optional_where(inner)
        self.finalize(plist, names, where)
        return inner

    def optional_where(self, names: _Names) -> tuple[int, int] | None:
        if not self.at_kw("WHERE"):
            return None
        self.i += 1
        start = self.tok.start
        self.expr(names)
        return start, self.toks[self.i - 1].end

    def with_clause(self, names: _Names) -> _Names:
        self.i += 1
        if self.at_kw("DISTINCT"):
            self.i += 1
        projected, star = self.projection(names)
        kept = frozenset(projected) | (names.strict if star else frozenset())
        new = _Names(kept, kept | (names.loose if star else frozenset()))
        self.order_skip_limit(_Names(new.strict, names.loose | new.loose))
        if self.at_kw("WHERE"):
            self.i += 1
            self.expr(new)
        return new

    def return_clause(self, names: _Names) -> None:
        self.i += 1
        if self.at_kw("DISTINCT"):
            self.i += 1
        projected, star = self.projection(names)
        # A pattern in ORDER BY may reference only what the projection guarantees (after an aggregation or
        # DISTINCT the earlier names are gone); an expression may read the earlier names too.
        kept = frozenset(projected) | (names.strict if star else frozenset())
        self.order_skip_limit(_Names(kept, names.loose | kept))

    def projection(self, names: _Names) -> tuple[set[str], bool]:
        out: set[str] = set()
        star = False
        if self.at_p("*"):
            self.i += 1
            star = True
            if not self.at_p(","):
                return out, star
            self.i += 1
        while True:
            start = self.i
            self.expr(names)
            if self.at_kw("AS"):
                self.i += 1
                name, _ = self.binding()
                out.add(name)
            elif self.i == start + 1 and self.toks[start].kind in ("name", "bname") \
                    and self.toks[start].value.upper() not in ("TRUE", "FALSE", "NULL"):
                out.add(self.toks[start].value)
            if self.at_p(","):
                self.i += 1
                continue
            return out, star

    def order_skip_limit(self, names: _Names) -> None:
        if self.at_kw("ORDER"):
            self.i += 1
            self.expect_kw("BY")
            while True:
                self.expr(names)
                if self.tok.kind == "name" and self.tok.value.upper() in ("ASC", "DESC", "ASCENDING", "DESCENDING"):
                    self.i += 1
                if self.at_p(","):
                    self.i += 1
                    continue
                break
        if self.at_kw("SKIP"):
            self.i += 1
            self.expr(names)
        if self.at_kw("LIMIT"):
            self.i += 1
            self.expr(names)

    def call_clause(self, names: _Names) -> _Names:
        call = self.tok
        self.i += 1
        if self.at_p("{") or self.at_p("("):
            if self.at_p("("):
                self.skip_balanced()
                if not self.at_p("{"):
                    self.syntax()
            self.refuse("call_subquery", call.start, "a CALL subquery is not allowed")
            self.skip_balanced()
            if self.at_kw("IN"):
                while self.tok.kind != "eof" and not (self.tok.kind == "name"
                                                      and self.tok.value.upper() in _CLAUSE_START):
                    self.i += 1
            return names
        if not self.at_name():
            self.syntax()
        parts = [self.tok]
        self.i += 1
        while self.at_p(".") and self.at_name(1):
            parts.append(self.peek(1))
            self.i += 2
        proc = ".".join(p.value for p in parts)
        if proc == FULLTEXT_PROCEDURE and all(p.kind == "name" for p in parts):
            return self.fulltext(names, call)
        self.refuse("procedure", call.start, f"the procedure {proc} may not be called here")
        if self.at_p("("):
            self.i += 1
            self.enter()
            if not self.at_p(")"):
                while True:
                    self.expr(names)
                    if self.at_p(","):
                        self.i += 1
                        continue
                    break
            self.expect_p(")")
            self.leave()
        if self.at_kw("YIELD"):
            self.i += 1
            bound: set[str] = set()
            if self.at_p("*"):
                self.i += 1
            else:
                while True:
                    field_name, _ = self.binding()
                    if self.at_kw("AS"):
                        self.i += 1
                        field_name, _ = self.binding()
                    bound.add(field_name)
                    if self.at_p(","):
                        self.i += 1
                        continue
                    break
            names = names.bind(bound)
            if self.at_kw("WHERE"):
                self.i += 1
                self.expr(names)
        return names

    def fulltext(self, names: _Names, call: _Tok) -> _Names:
        self.expect_p("(")
        self.enter()
        first = self.tok
        if first.kind == "string" and first.value == FULLTEXT_INDEX and (self.at_p(",", 1) or self.at_p(")", 1)):
            self.i += 1
        else:
            self.refuse("fulltext_form", first.start, f"the index must be the literal '{FULLTEXT_INDEX}'")
            if not self.at_p(",") and not self.at_p(")"):
                self.expr(names)
        if self.at_p(","):
            self.i += 1
            second = self.tok
            if second.kind in ("param", "string") and (self.at_p(",", 1) or self.at_p(")", 1)):
                self.i += 1
            else:
                self.refuse("fulltext_form", second.start, "the search text must be a parameter or a string")
                self.expr(names)
            while self.at_p(","):
                self.refuse("fulltext_form", self.tok.start, "the fulltext search takes exactly two arguments")
                self.i += 1
                self.expr(names)
        else:
            self.refuse("fulltext_form", self.tok.start, "the fulltext search takes exactly two arguments")
        self.expect_p(")")
        self.leave()
        if not self.at_kw("YIELD"):
            self.refuse("fulltext_form", self.tok.start, "the fulltext search must YIELD node")
            return names
        self.i += 1
        bound: set[str] = set()
        node: tuple[str, str, int] | None = None
        star = False
        if self.at_p("*"):
            self.refuse("fulltext_form", self.tok.start, "YIELD * is not allowed; YIELD node")
            self.i += 1
            star = True
            bound |= {"node", "score"}
        else:
            while True:
                t = self.tok
                field_name, field_raw = self.binding()
                if t.kind != "name" or field_name not in ("node", "score"):
                    self.refuse("fulltext_form", t.start, "the fulltext search yields only node and score")
                alias, alias_raw = field_name, field_raw
                if self.at_kw("AS"):
                    self.i += 1
                    alias, alias_raw = self.binding()
                if t.kind == "name" and field_name == "node":
                    node = (alias, alias_raw, t.start)
                bound.add(alias)
                if self.at_p(","):
                    self.i += 1
                    continue
                break
            if node is None:
                self.refuse("fulltext_form", call.start, "the fulltext search must YIELD node")
        end_off = self.toks[self.i - 1].end
        names = names.bind(bound)
        where = self.optional_where(names)
        if node is not None and not star:
            alias, alias_raw, offset = node
            element = self.gen("p")
            clause = SCOPE_CLAUSE_TEMPLATE.format(element=element, var=alias_raw, param=SCOPE_PARAM)
            self.add_where([clause], where, end_off)
            self.injected.append((offset, f"{alias_raw}: sample clause"))
        return names

    # ------------------------------------------------------------------ patterns
    def pattern_list(self, names: _Names) -> _PatternList:
        paths = [self.path(names)]
        while self.at_p(","):
            self.i += 1
            paths.append(self.path(names))
        bound: set[str] = set()
        for path in paths:
            if path.name:
                bound.add(path.name)
            for node in path.nodes:
                if node.var and node.var not in names.strict:
                    bound.add(node.var)
            for rel in path.rels:
                if rel.var:
                    bound.add(rel.var)
        return _PatternList(paths, self.toks[self.i - 1].end, bound)

    def path(self, names: _Names) -> _Path:
        start = self.tok.start
        path = _Path(start_off=start, node_start_off=start)
        if self.at_name() and self.at_p("=", 1):
            path.name, path.name_raw = self.binding()
            self.i += 1
        if self.tok.kind == "name" and self.tok.value.upper() in ("SHORTESTPATH", "ALLSHORTESTPATHS") \
                and self.at_p("(", 1):
            self.refuse("path_selector", self.tok.start, f"{self.tok.value} is not allowed")
            self.i += 1
            self.skip_balanced()
            path.opaque = True
            return path
        if self.tok.kind == "name" and self.tok.value.upper() in ("SHORTEST", "ANY", "ALL"):
            self.refuse("path_selector", self.tok.start, "a path selector is not allowed")
            while self.tok.kind != "eof" and not self.at_p("("):
                self.i += 1
        path.node_start_off = self.tok.start
        path.nodes.append(self.node(names))
        while self.at_p("-") or self.at_p("<"):
            path.rels.append(self.rel(names))
            if self.at_p("{") or self.at_p("+") or self.at_p("*"):
                self.refuse("path_selector", self.tok.start, "a quantified relationship is not allowed")
                if self.at_p("{"):
                    self.skip_balanced()
                else:
                    self.i += 1
            path.nodes.append(self.node(names))
        if any(node.refused and node.var is None and node.key == "qpp" for node in path.nodes):
            path.opaque = True
        return path

    def node(self, names: _Names) -> _Node:
        open_tok = self.tok
        if not self.at_p("("):
            self.syntax()
        if self.at_p("(", 1):
            self.refuse("path_selector", open_tok.start, "a quantified path pattern is not allowed")
            self.skip_balanced()
            if self.at_p("{"):
                self.skip_balanced()
            elif self.at_p("+") or self.at_p("*"):
                self.i += 1
            return _Node(open_off=open_tok.start, refused=True, key="qpp")
        self.i += 1
        node = _Node(open_off=open_tok.start)
        if self.at_name() and not self.at_kw("WHERE") and not self.at_kw("IS"):
            node.var, node.var_raw = self.binding()
        if self.at_p(":"):
            node.labels, node.refused = self.pattern_labels(rel=False)
        elif self.at_kw("IS"):
            self.refuse("label_expression", self.tok.start, "an IS label expression is not allowed in a pattern")
            self.i += 1
            self.skip_label_tokens(rel=False)
            node.refused = True
        if self.at_p("{"):
            self.pattern_map(names)
        elif self.tok.kind == "param":
            self.syntax("a parameter map in a pattern is not allowed")
        if self.at_kw("WHERE"):
            self.refuse("inline_where", self.tok.start, "a WHERE inside a node pattern is not allowed")
            self.i += 1
            self.expr(names.bind({node.var} if node.var else set()))
        self.expect_p(")")
        return node

    def pattern_labels(self, rel: bool) -> tuple[list[str], bool]:
        self.expect_p(":")
        labels: list[str] = []
        while True:
            if self.at_name():
                labels.append(self.tok.value)
                self.i += 1
            else:
                self.refuse("label_expression", self.tok.start, "a label expression other than : and & is not "
                                                                "allowed in a pattern")
                self.skip_label_tokens(rel)
                return labels, True
            if self.at_p(":") or self.at_p("&"):
                self.i += 1
                continue
            if self.at_p("|") or self.at_p("!"):
                self.refuse("label_expression", self.tok.start, "a label expression other than : and & is not "
                                                                "allowed in a pattern")
                self.skip_label_tokens(rel)
                return labels, True
            return labels, False

    def skip_label_tokens(self, rel: bool) -> None:
        depth = 0
        while True:
            t = self.tok
            if t.kind == "eof":
                return
            if t.kind == "punct" and t.value == "(":
                depth += 1
            elif t.kind == "punct" and t.value == ")":
                if depth == 0:
                    return
                depth -= 1
            elif depth == 0 and t.kind == "punct" and t.value in ("{", "]", "*"):
                return
            elif depth == 0 and t.kind == "name" and t.value.upper() == "WHERE":
                return
            elif not (t.kind in ("name", "bname", "param") or (t.kind == "punct" and t.value in ":&|!%$")):
                if depth == 0:
                    return
            self.i += 1

    def pattern_map(self, names: _Names) -> None:
        self.expect_p("{")
        self.enter()
        if self.at_p("}"):
            self.i += 1
            self.leave()
            return
        while True:
            if not self.at_name():
                self.syntax()
            key = self.tok
            if key.value.lower() in _HIDDEN:
                self.refuse("hidden_property", key.start, f"the property {key.value} is hidden")
            self.i += 1
            self.expect_p(":")
            self.expr(names)
            if self.at_p(","):
                self.i += 1
                continue
            break
        self.expect_p("}")
        self.leave()

    def rel(self, names: _Names) -> _Rel:
        start = self.tok
        if self.at_p("<"):
            self.i += 1
        self.expect_p("-")
        if self.at_p("-"):
            self.i += 1
            if self.at_p(">"):
                self.i += 1
            self.refuse("relationship_type", start.start, "a relationship without exactly one type")
            return _Rel(start.start, None, False, True)
        self.expect_p("[")
        var = None
        if self.at_name() and not self.at_kw("WHERE"):
            var, _ = self.binding()
        rtype: str | None = None
        refused = False
        if self.at_p(":"):
            self.i += 1
            if self.at_name():
                rtype = self.tok.value
                self.i += 1
                if self.at_p("|") or self.at_p(":") or self.at_p("&") or self.at_p("!"):
                    rtype = None
            if rtype is None:
                self.refuse("relationship_type", start.start, "a relationship without exactly one type")
                self.skip_label_tokens(rel=True)
                refused = True
        else:
            self.refuse("relationship_type", start.start, "a relationship without exactly one type")
            refused = True
        varlen = False
        if self.at_p("*"):
            varlen = True
            self.i += 1
            if self.tok.kind == "number" and self.tok.value.isdigit():
                self.i += 1
            if self.at_p(".."):
                self.i += 1
                if self.tok.kind == "number" and self.tok.value.isdigit():
                    self.i += 1
        if self.at_p("{"):
            self.pattern_map(names)
        elif self.tok.kind == "param":
            self.syntax("a parameter map in a pattern is not allowed")
        if self.at_kw("WHERE"):
            self.refuse("inline_where", self.tok.start, "a WHERE inside a relationship pattern is not allowed")
            self.i += 1
            self.expr(names.bind({var} if var else set()))
        self.expect_p("]")
        self.expect_p("-")
        if self.at_p(">"):
            self.i += 1
        if not refused:
            if rtype in FIXED_RELATIONSHIPS:
                if varlen:
                    self.refuse("variable_length", start.start, f"variable length on {rtype} is not allowed")
                    refused = True
            elif rtype != LINEAGE_RELATIONSHIP:
                self.refuse("relationship_type", start.start, f"the relationship {rtype} may not be walked")
                refused = True
        return _Rel(start.start, rtype, varlen, refused, var)

    # ------------------------------------------------------------------ classification and injection
    def finalize(self, plist: _PatternList, names: _Names, where: tuple[int, int] | None,
                 bare_first_off: int | None = None) -> None:
        seq_start = self.seq
        vertices: dict[Any, _Vertex] = {}
        order: list[_Vertex] = []
        typed: dict[Any, list[tuple[Any, str | None]]] = {}

        def vertex_of(node: _Node) -> _Vertex:
            if node.key is None or node.key == "qpp":
                if node.var:
                    node.key = ("var", node.var)
                else:
                    self.anon += 1
                    node.key = ("anon", self.anon)
            v = vertices.get(node.key)
            if v is None:
                v = _Vertex(key=node.key, first=node, var=node.var, var_raw=node.var_raw)
                vertices[node.key] = v
                order.append(v)
                typed[node.key] = []
                if node.var and node.var in names.strict:
                    v.reference = True
                elif node.var and node.var in names.loose:
                    self.f.add("syntax", node.open_off, f"the name {node.var} is not bound here")
                    raise _Stop
            v.labels.update(node.labels)
            v.refused = v.refused or node.refused
            return v

        live_paths = [p for p in plist.paths if not p.opaque]
        for path in live_paths:
            path_vertices = [vertex_of(node) for node in path.nodes]
            for k, rel in enumerate(path.rels):
                a, b = path_vertices[k], path_vertices[k + 1]
                rtype = None if rel.refused else rel.rtype
                typed[a.key].append((b.key, rtype))
                typed[b.key].append((a.key, rtype))
                if rel.rtype == LINEAGE_RELATIONSHIP and not rel.refused:
                    a.derived_end = b.derived_end = True
            path.varlen = any(r.varlen and r.rtype == LINEAGE_RELATIONSHIP and not r.refused for r in path.rels)
            path.mixed = path.varlen and any(r.rtype != LINEAGE_RELATIONSHIP for r in path.rels)
            if path.mixed:
                self.refuse("variable_length", path.start_off,
                            "a path that mixes variable-length DERIVED_FROM with another relationship")
            if path.varlen and not path.mixed:
                for v in path_vertices:
                    v.in_varlen = True

        for v in order:
            if v.refused:
                v.kind = "none"
            elif v.reference:
                v.kind = "reference"
                v.ref_text = v.var_raw
            elif not v.labels:
                if v.derived_end:
                    v.kind = "sample"
                else:
                    v.kind = "none"
                    self.refuse("unlabelled_node", v.first.open_off,
                                "a node without a label can only be an end of DERIVED_FROM")
            else:
                kinds = {_label_kind(label) for label in v.labels}
                if kinds == {"sample"}:
                    v.kind = "sample"
                elif kinds == {"project"}:
                    v.kind = "project"
                elif kinds == {"joined"} and len(v.labels) == 1:
                    v.kind = "joined"
                else:
                    v.kind = "none"
                    self.refuse("label_not_allowed", v.first.open_off,
                                f"the label {':'.join(sorted(v.labels))} may not be read")
            if v.kind in ("sample", "project") and v.var_raw:
                v.ref_text = v.var_raw

        joined_lines: list[tuple[int, str]] = []
        pending_joined = [v for v in order if v.kind == "joined"]

        clauses: list[str] = []
        emitted: set[Any] = set()
        for path in live_paths:
            if path.varlen and not path.mixed:
                pname = path.name_raw
                if not pname:
                    pname = self.gen("path")
                    self.insert(path.node_start_off, f"{pname} = ")
                node_name, element = self.gen("m"), self.gen("p")
                clauses.append(PATH_CLAUSE_TEMPLATE.format(node=node_name, path=pname, element=element,
                                                           param=SCOPE_PARAM))
                covered = list(dict.fromkeys(n.var_raw for n in path.nodes if n.var_raw))
                line = f"{pname}: every node" + (f" ({', '.join(covered)})" if covered else "")
                self.injected.append((path.start_off, line))
            for node in path.nodes:
                v = vertices[node.key]
                if v.key in emitted:
                    continue
                if v.kind == "sample" and not v.in_varlen:
                    emitted.add(v.key)
                    ref = self.name_for(v)
                    clauses.append(SCOPE_CLAUSE_TEMPLATE.format(element=self.gen("p"), var=ref, param=SCOPE_PARAM))
                    self.injected.append((v.first.open_off, f"{ref}: sample clause"))
                elif v.kind == "project":
                    emitted.add(v.key)
                    ref = self.name_for(v)
                    clauses.append(PROJECT_CLAUSE_TEMPLATE.format(var=ref, param=SCOPE_PARAM))
                    self.injected.append((v.first.open_off, f"{ref}: project clause"))

        proven = self.prove_joined(pending_joined, typed, vertices)
        for v in pending_joined:
            label = next(iter(v.labels))
            who = f"{v.var_raw} ({label})" if v.var_raw else f"(:{label})"
            if v.key in proven:
                joined_lines.append((v.first.open_off, f"{who}: joined to {self.display(vertices[proven[v.key]])}"))
            elif not self.component_has_refusal(v.key, typed, vertices):
                self.refuse("unjoined_node", v.first.open_off,
                            f"{who} is not joined to a sample it holds, to a study it holds or to a project")
        self.joined.extend(joined_lines)

        if clauses:
            if bare_first_off is not None:
                self.insert(bare_first_off, "MATCH ", seq=seq_start + 0.5)
            self.add_where(clauses, where, plist.end_off)

    def name_for(self, v: _Vertex) -> str:
        if v.ref_text:
            return v.ref_text
        name = self.gen("n")
        self.insert(v.first.open_off + 1, name)
        v.ref_text = name
        return name

    def display(self, v: _Vertex) -> str:
        if v.ref_text:
            return v.ref_text
        if v.var_raw:
            return v.var_raw
        return "(" + "".join(f":{label}" for label in sorted(v.labels)) + ")"

    @staticmethod
    def prove_joined(pending: list[_Vertex], typed: dict[Any, list[tuple[Any, str | None]]],
                     vertices: dict[Any, _Vertex]) -> dict[Any, Any]:
        """{joined vertex: the neighbour that proves it}. A joined node is proven only as the container of something
        visible, through the one relationship type that makes it so (every relationship in the graph points from the
        contained to the container, so the type alone fixes the direction of a match):

        - a Study, by IN_STUDY to a scoped sample or a bound name (the study of a visible sample);
        - an Investigation, by IN_INVESTIGATION to a proven Study or a bound name (the investigation of such a
          study), or by IN_PROJECT to a scoped project or a bound name (an investigation of the caller's project);
        - a Person, by MEMBER_OF to a scoped project or a bound name (a member of the caller's project).

        Walking down from a container is never a proof: a study reached from its investigation may hold only samples
        the caller cannot see.
        """
        proven: dict[Any, Any] = {}
        changed = True
        while changed:
            changed = False
            for v in pending:
                if v.key in proven:
                    continue
                label = next(iter(v.labels))
                for other_key, rtype in typed.get(v.key, []):
                    other = vertices[other_key]
                    bound = other.kind == "reference"
                    if label == "Study":
                        ok = rtype == "IN_STUDY" and (bound or other.kind == "sample")
                    elif label == "Investigation":
                        ok = (rtype == "IN_INVESTIGATION" and (bound or (other.key in proven
                                                                          and "Study" in other.labels))) \
                            or (rtype == "IN_PROJECT" and (bound or other.kind == "project"))
                    else:  # Person
                        ok = rtype == "MEMBER_OF" and (bound or other.kind == "project")
                    if ok:
                        proven[v.key] = other_key
                        changed = True
                        break
        return proven

    @staticmethod
    def component_has_refusal(start: Any, typed: dict[Any, list[tuple[Any, str | None]]],
                              vertices: dict[Any, _Vertex]) -> bool:
        """Is a refused node or relationship connected to `start`? Then the statement is refused already, and an
        unjoined node beside it would only repeat that."""
        seen, stack = set(), [start]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            if vertices[node].kind == "none":
                return True
            for other, rtype in typed.get(node, []):
                if rtype is None:
                    return True
                stack.append(other)
        return False

    def add_where(self, clauses: list[str], where: tuple[int, int] | None, end_off: int) -> None:
        text = " AND ".join(clauses)
        if where is None:
            self.insert(end_off, " WHERE " + text)
        else:
            self.insert(where[0], "(")
            self.insert(where[1], ") AND " + text)

    # ------------------------------------------------------------------ expressions
    def expr(self, names: _Names) -> None:
        self.xor_(names)
        while self.at_kw("OR"):
            self.i += 1
            self.xor_(names)

    def xor_(self, names: _Names) -> None:
        self.and_(names)
        while self.at_kw("XOR"):
            self.i += 1
            self.and_(names)

    def and_(self, names: _Names) -> None:
        self.not_(names)
        while self.at_kw("AND"):
            self.i += 1
            self.not_(names)

    def not_(self, names: _Names) -> None:
        while self.at_kw("NOT"):
            self.i += 1
        self.comparison(names)

    def pattern_operator(self, k: int) -> bool:
        """Is the operator at token k the start of a relationship (a pattern in an expression)?"""
        t, nxt = self.toks[k], self.toks[min(k + 1, len(self.toks) - 1)]
        prev_paren = k > 0 and (k - 1) in self.paren_closes
        if t.value == "-":
            if nxt.kind == "punct" and nxt.start == t.end and nxt.value in ("-", ">"):
                return True
            return prev_paren and nxt.kind == "punct" and nxt.value in ("[", "-")
        if t.value == "<":
            if nxt.kind == "punct" and nxt.value == "-" and (nxt.start == t.end or prev_paren):
                return True
        return False

    def refuse_pattern_expression(self) -> None:
        self.refuse("pattern_expression", self.tok.start, "a pattern inside an expression; use EXISTS { }")
        self.skip_to_enclosing_close()

    def comparison(self, names: _Names) -> None:
        self.additive(names)
        while True:
            t = self.tok
            if t.kind == "punct" and t.value in _COMPARISON:
                if self.pattern_operator(self.i):
                    self.refuse_pattern_expression()
                    return
                self.i += 1
                self.additive(names)
            elif self.at_kw("IS"):
                self.i += 1
                if self.at_kw("NOT"):
                    self.i += 1
                self.expect_kw("NULL")
            elif self.at_kw("IN") or self.at_kw("CONTAINS"):
                self.i += 1
                self.additive(names)
            elif self.at_kw("STARTS") or self.at_kw("ENDS"):
                self.i += 1
                self.expect_kw("WITH")
                self.additive(names)
            else:
                return

    def additive(self, names: _Names) -> None:
        self.multiplicative(names)
        while self.at_p("+") or self.at_p("-"):
            if self.pattern_operator(self.i):
                self.refuse_pattern_expression()
                return
            self.i += 1
            self.multiplicative(names)

    def multiplicative(self, names: _Names) -> None:
        self.power(names)
        while self.at_p("*") or self.at_p("/") or self.at_p("%"):
            self.i += 1
            self.power(names)

    def power(self, names: _Names) -> None:
        self.unary(names)
        while self.at_p("^"):
            self.i += 1
            self.unary(names)

    def unary(self, names: _Names) -> None:
        if self.at_p("-") or self.at_p("+"):
            if self.at_p("-") and self.pattern_operator(self.i):
                self.refuse_pattern_expression()
                return
            self.i += 1
        self.postfix(names)

    def postfix(self, names: _Names) -> None:
        self.atom(names)
        while True:
            if self.at_p("."):
                key = self.peek(1)
                if key.kind not in ("name", "bname"):
                    self.i += 1
                    self.syntax()
                if key.value.lower() in _HIDDEN:
                    self.refuse("hidden_property", key.start, f"the property {key.value} is hidden")
                self.i += 2
            elif self.at_p("["):
                self.subscript(names)
            elif self.at_p("{"):
                self.map_projection(names)
            else:
                return

    def subscript(self, names: _Names) -> None:
        open_tok = self.tok
        self.enter()
        self.i += 1
        save = self.i

        def integer() -> bool:
            if self.at_p("-") and self.peek(1).kind == "number" and self.peek(1).value.isdigit():
                self.i += 2
                return True
            if self.tok.kind == "number" and self.tok.value.isdigit():
                self.i += 1
                return True
            return False

        low = integer()
        if self.at_p(".."):
            self.i += 1
            high = integer()
            ok = (low or high) and self.at_p("]")
        else:
            ok = low and self.at_p("]")
        if not ok:
            self.i = save
            self.refuse("dynamic_property", open_tok.start, "a subscript that is not an integer literal")
            if not self.at_p(".."):
                self.expr(names)
            if self.at_p(".."):
                self.i += 1
                if not self.at_p("]"):
                    self.expr(names)
        self.expect_p("]")
        self.leave()

    def map_projection(self, names: _Names) -> None:
        self.enter()
        self.expect_p("{")
        if self.at_p("}"):
            self.i += 1
            self.leave()
            return
        while True:
            if self.at_p("."):
                if self.at_p("*", 1):
                    self.refuse("whole_properties", self.tok.start, "a map projection of every property")
                    self.i += 2
                elif self.at_name(1):
                    key = self.peek(1)
                    if key.value.lower() in _HIDDEN:
                        self.refuse("hidden_property", key.start, f"the property {key.value} is hidden")
                    self.i += 2
                else:
                    self.i += 1
                    self.syntax()
            elif self.at_name() and self.at_p(":", 1):
                self.i += 2
                self.expr(names)
            elif self.at_name():
                self.variable(names)
            else:
                self.syntax()
            if self.at_p(","):
                self.i += 1
                continue
            break
        self.expect_p("}")
        self.leave()

    def variable(self, names: _Names) -> None:
        t = self.tok
        if t.kind == "name" and t.value.upper() in _RESERVED:
            self.syntax()
        if t.value not in names.loose:
            self.syntax(f"the name {t.value} is not bound here")
        self.i += 1

    def label_test(self) -> None:
        self.expect_p(":")
        while True:
            if self.at_p("!"):
                self.i += 1
            if self.at_name():
                self.i += 1
            else:
                self.refuse("label_expression", self.tok.start, "a label test other than :, &, | and !")
                self.skip_label_tokens(rel=False)
                return
            if self.at_p(":") or self.at_p("&") or self.at_p("|"):
                self.i += 1
                continue
            return

    def atom(self, names: _Names) -> None:
        t = self.tok
        if t.kind in ("number", "string", "param"):
            self.i += 1
            return
        if t.kind == "punct":
            if t.value == "(":
                self.enter()
                self.i += 1
                self.expr(names)
                close = self.i
                self.expect_p(")")
                self.paren_closes.add(close)
                self.leave()
                return
            if t.value == "[":
                self.list_or_comprehension(names)
                return
            if t.value == "{":
                self.map_literal(names)
                return
            self.syntax()
        if t.kind == "bname":
            self.variable(names)
            if self.at_p(":"):
                self.label_test()
            return
        if t.kind != "name":
            self.syntax()
        word = t.value.upper()
        if word in ("TRUE", "FALSE", "NULL"):
            self.i += 1
            return
        if word == "CASE":
            self.case(names)
            return
        if word in ("EXISTS", "COUNT", "COLLECT") and self.at_p("{", 1):
            self.subquery(names)
            return
        if word == "COUNT" and self.at_p("(", 1) and self.at_p("*", 2) and self.at_p(")", 3):
            self.i += 4
            return
        if word in ("ANY", "ALL", "NONE", "SINGLE") and self.at_p("(", 1) and self.at_name(2) and self.at_kw("IN", 3):
            self.quantifier(names)
            return
        if word == "REDUCE" and self.at_p("(", 1):
            self.reduce(names)
            return
        j = self.i
        while self.toks[j + 1].kind == "punct" and self.toks[j + 1].value == "." \
                and self.toks[j + 2].kind in ("name", "bname"):
            j += 2
        if self.toks[j + 1].kind == "punct" and self.toks[j + 1].value == "(":
            self.call(names, j)
            return
        self.variable(names)
        if self.at_p(":"):
            self.label_test()

    def call(self, names: _Names, last: int) -> None:
        start = self.tok
        parts = self.toks[self.i:last + 1:2]
        name = ".".join(p.value for p in parts)
        lowered = name.lower()
        self.i = last + 1
        if lowered in ("shortestpath", "allshortestpaths"):
            self.refuse("path_selector", start.start, f"{name} is not allowed")
            self.skip_balanced()
            return
        if lowered == "properties":
            self.refuse("whole_properties", start.start, "properties() reads every property of a value")
        elif any(p.kind == "bname" and "." in p.value for p in parts) or not self.allowed(lowered):
            self.refuse("function_not_allowed", start.start, f"the function {name} is not allowed")
        self.enter()
        self.expect_p("(")
        if self.at_kw("DISTINCT"):
            self.i += 1
        if not self.at_p(")"):
            while True:
                self.expr(names)
                if self.at_p(","):
                    self.i += 1
                    continue
                break
        self.expect_p(")")
        self.leave()

    @staticmethod
    def allowed(lowered: str) -> bool:
        return (lowered in FUNCTION_ALLOWLIST or lowered in FUNCTION_NAMESPACE_ALLOWLIST
                or any(lowered.startswith(family) for family in PURE_APOC_FAMILIES))

    def quantifier(self, names: _Names) -> None:
        self.enter()
        self.i += 2
        local, _ = self.binding()
        self.expect_kw("IN")
        self.expr(names)
        if self.at_kw("WHERE"):
            self.i += 1
            self.expr(names.bind({local}))
        self.expect_p(")")
        self.leave()

    def reduce(self, names: _Names) -> None:
        self.enter()
        self.i += 2
        acc, _ = self.binding()
        self.expect_p("=")
        self.expr(names)
        self.expect_p(",")
        local, _ = self.binding()
        self.expect_kw("IN")
        self.expr(names)
        self.expect_p("|")
        self.expr(names.bind({acc, local}))
        self.expect_p(")")
        self.leave()

    def list_or_comprehension(self, names: _Names) -> None:
        self.enter()
        self.i += 1
        if self.at_name() and self.at_kw("IN", 1) and not (self.tok.kind == "name"
                                                           and self.tok.value.upper() in _RESERVED):
            local, _ = self.binding()
            self.i += 1
            self.expr(names)
            inner = names.bind({local})
            if self.at_kw("WHERE"):
                self.i += 1
                self.expr(inner)
            if self.at_p("|"):
                self.i += 1
                self.expr(inner)
        elif not self.at_p("]"):
            while True:
                self.expr(names)
                if self.at_p(","):
                    self.i += 1
                    continue
                break
        self.expect_p("]")
        self.leave()

    def map_literal(self, names: _Names) -> None:
        self.enter()
        self.expect_p("{")
        if not self.at_p("}"):
            while True:
                if not self.at_name():
                    self.syntax()
                self.i += 1
                self.expect_p(":")
                self.expr(names)
                if self.at_p(","):
                    self.i += 1
                    continue
                break
        self.expect_p("}")
        self.leave()

    def case(self, names: _Names) -> None:
        self.enter()
        self.i += 1
        if not self.at_kw("WHEN"):
            self.expr(names)
        if not self.at_kw("WHEN"):
            self.syntax()
        while self.at_kw("WHEN"):
            self.i += 1
            self.expr(names)
            self.expect_kw("THEN")
            self.expr(names)
        if self.at_kw("ELSE"):
            self.i += 1
            self.expr(names)
        self.expect_kw("END")
        self.leave()

    def subquery(self, names: _Names) -> None:
        keyword = self.tok
        self.i += 1
        if keyword.value.upper() == "COLLECT":
            self.refuse("collect_subquery", keyword.start, "a COLLECT subquery is not allowed")
            self.skip_balanced()
            return
        self.enter()
        self.expect_p("{")
        inner = _Names(names.strict, names.strict)
        if self.at_p("(") or (self.at_name() and self.at_p("=", 1)):
            first = self.tok.start
            plist = self.pattern_list(inner)
            where = self.optional_where(inner.bind(plist.bound))
            self.finalize(plist, inner, where, bare_first_off=first)
        else:
            if not (self.at_kw("MATCH") or self.at_kw("CALL") or self.at_kw("UNION")):
                self.syntax()
            while not self.at_p("}"):
                if self.at_kw("MATCH"):
                    self.i += 1
                    plist = self.pattern_list(inner)
                    bound = inner.bind(plist.bound)
                    where = self.optional_where(bound)
                    self.finalize(plist, inner, where)
                    inner = bound
                elif self.at_kw("CALL"):
                    inner = self.call_clause(inner)
                elif self.at_kw("UNION"):
                    self.refuse("union", self.tok.start, "UNION joins a second statement")
                    self.i += 1
                else:
                    self.syntax()
        self.expect_p("}")
        self.leave()

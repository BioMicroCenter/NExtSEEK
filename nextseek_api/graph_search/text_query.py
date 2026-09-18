"""The Sample Search page's query text, parsed into a boolean tree for graph_search's ``extensions.query``.

advanced_search read this text with ``seek/search.py::Search.designSearchPubmed``; the rows it returned, and its
parser's defects, are in this package's README ("What advanced_search returned"). This parser keeps its vocabulary and
reads every shape the page's Add button writes the same way:

- Operators are the upper-case words ``AND``, ``OR`` and ``NOT`` standing alone: whitespace, a parenthesis or an end
  of the text on each side. A lower-case ``and`` is part of a term.
- Parentheses group. A level (the whole text, or the inside of one pair) joins operands with binary operators.
  ``a NOT b`` is ``a AND NOT b``, so ``AND`` and ``NOT`` may share a level; ``OR`` may not share one with either
  (advanced_search had no precedence, so the text must say it with parentheses).
- ``NOT`` where an operand starts (the start of a level, or after a binary operator) negates that operand.
- A term is the text between operators and parentheses, trimmed. ``term[TYPE]``: with exactly one ``[...]`` pair,
  the text before it (trimmed) is the term and the trimmed, upper-cased inside is the tag; text after ``]`` is
  dropped, as ``Search.__parseKeyword`` dropped it. Any other brackets are part of the term.

It refuses, with a reason a person can act on, what advanced_search turned into a literal phrase or garbage: an
operator with no term beside it, ``OR`` mixed with ``AND`` or ``NOT`` on one level, unbalanced or empty
parentheses, and a term or group directly beside a group. What a term and a tag match is the query builder's
(``query.py``).

Pure: no Django, no database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Union

MAX_TERMS = 64
MAX_DEPTH = 32

_OPERATOR_RE = re.compile(r"(?<!\S)(AND|OR|NOT)(?!\S)")
_PAREN_RE = re.compile(r"([()])")


class QueryTextInvalid(ValueError):
    """Text the parser cannot read; ``str(exc)`` says why."""


@dataclass(frozen=True)
class Term:
    text: str                 # the term as typed, trimmed, without its tag; "" for a tag alone
    tag: Optional[str]        # the trimmed, upper-cased tag, or None when the term has none
    index: int                # position among the text's terms, from 0


@dataclass(frozen=True)
class Not:
    operand: "Node"


@dataclass(frozen=True)
class All:
    operands: tuple


@dataclass(frozen=True)
class Any:
    operands: tuple


Node = Union[Term, Not, All, Any]


def _term(raw: str, index: int) -> Term:
    if raw.count("[") == 1 and raw.count("]") == 1:
        opening, closing = raw.index("["), raw.index("]")
        if opening < closing:
            return Term(raw[:opening].strip(), raw[opening + 1:closing].strip().upper(), index)
    return Term(raw, None, index)


def _tokens(text: str) -> list:
    """``"("``, ``")"``, ``("OP", word)`` and ``("TERM", text)`` in order; empty text between tokens is dropped."""
    tokens: list = []
    for piece in _PAREN_RE.split(text):
        if piece in ("(", ")"):
            tokens.append(piece)
            continue
        for i, part in enumerate(_OPERATOR_RE.split(piece)):
            if i % 2:
                tokens.append(("OP", part))
            elif part.strip():
                tokens.append(("TERM", part.strip()))
    return tokens


def _shown(token) -> str:
    return f"'{token[1]}'" if isinstance(token, tuple) else f"'{token}'"


class _Parser:
    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0
        self.count = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def level(self, depth: int) -> Node:
        if depth > MAX_DEPTH:
            raise QueryTextInvalid(f"Too many nested parentheses: at most {MAX_DEPTH}.")
        operands = [self.operand(depth, None)]
        operators: list[str] = []
        while True:
            token = self.peek()
            if token is None or token == ")":
                break
            if not (isinstance(token, tuple) and token[0] == "OP"):
                before = self.tokens[self.pos - 1]
                raise QueryTextInvalid(f"Put AND, OR or NOT between {_shown(before)} and {_shown(token)}.")
            self.pos += 1
            operators.append(token[1])
            operands.append(self.operand(depth, token[1]))
        kinds = set(operators)
        if "OR" in kinds and len(kinds) > 1:
            raise QueryTextInvalid("Use parentheses to combine OR with AND or NOT, as in (a OR b) AND c.")
        if not operators:
            return operands[0]
        if kinds == {"OR"}:
            return Any(tuple(operands))
        joined = [operands[0]] + [Not(o) if op == "NOT" else o for op, o in zip(operators, operands[1:])]
        return All(tuple(joined))

    def operand(self, depth: int, after: Optional[str]) -> Node:
        token = self.peek()
        if token is None or token == ")":
            if after is not None:
                raise QueryTextInvalid(f"{after} needs a term after it.")
            if token == ")":
                raise QueryTextInvalid("The query has empty parentheses." if self.pos
                                       and self.tokens[self.pos - 1] == "(" else "A ')' has no matching '('.")
            raise QueryTextInvalid("The query has no search term.")
        if isinstance(token, tuple) and token[0] == "OP":
            if token[1] == "NOT":
                self.pos += 1
                return Not(self.operand(depth, "NOT"))
            raise QueryTextInvalid(f"{token[1]} needs a term before it.")
        self.pos += 1
        if token == "(":
            node = self.level(depth + 1)
            if self.peek() != ")":
                raise QueryTextInvalid("A '(' has no matching ')'.")
            self.pos += 1
            return node
        if self.count >= MAX_TERMS:
            raise QueryTextInvalid(f"The query has more than {MAX_TERMS} terms; use at most {MAX_TERMS}.")
        self.count += 1
        return _term(token[1], self.count - 1)


def parse(text: str) -> Node:
    """The tree of ``text``; raises ``QueryTextInvalid`` with the reason when it cannot be read."""
    parser = _Parser(_tokens(str(text or "")))
    node = parser.level(0)
    if parser.peek() == ")":
        raise QueryTextInvalid("A ')' has no matching '('.")
    return node


def terms(node: Node) -> list[Term]:
    """Every term of the tree in text order, negated ones included."""
    if isinstance(node, Term):
        return [node]
    if isinstance(node, Not):
        return terms(node.operand)
    return [leaf for operand in node.operands for leaf in terms(operand)]

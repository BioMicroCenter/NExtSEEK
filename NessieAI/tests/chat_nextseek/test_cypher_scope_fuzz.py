"""
scope_cypher never raises, whatever it is handed.

A seeded mutation fuzz over the accepted corpus (token deletions, insertions and swaps, plus insertions of
characters that trip lexers) and over random printable noise. Every mutant must come back Scoped or Refused, and a
Scoped mutant must still be its input plus insertions only, with the scope parameter bound.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 5.2 and 11.1.
"""
from __future__ import annotations

import random
import re

from chat_nextseek import cypher_scope
from chat_nextseek.cypher_scope import Refused, Scoped, scope_cypher
from chat_nextseek.graph_scope import SCOPE_PARAM, GraphScope

from NessieAI.tests.chat_nextseek.graph_scope.battery import ACCEPTED, REFUSALS, TAUGHT

CALLER = GraphScope.for_projects([1, 2], source="test")
SEED = 20260918
MUTANTS_PER_STATEMENT = 40

_TOKEN = re.compile(r"\s+|[A-Za-z_][A-Za-z0-9_]*|\d+|'[^']*'|\"[^\"]*\"|`[^`]*`|\$\w+|\.\.|->|<-|--|.", re.DOTALL)
_POOL = [
    "(", ")", "[", "]", "{", "}", "-", ">", "<", ":", ",", ".", "..", "*", "|", "&", "!", "%", "=", "$", "$x", ";",
    "'", '"', "`", "\\", "/*", "*/", "//", "\n", " ", "\u00a0", "\u00e9", "MATCH", "OPTIONAL", "WHERE", "RETURN",
    "WITH", "UNWIND", "CALL", "YIELD", "UNION", "EXISTS", "COUNT", "COLLECT", "CASE", "WHEN", "THEN", "END", "AS",
    "AND", "OR", "NOT", "IN", "IS", "NULL", "(n)", "(:Sample)", "(x:Study)", "-[:DERIVED_FROM*1..3]->", "-->",
    "-[r]-", "{.*}", "s.parent_titles", "properties(s)", "__scope_p1", "shortestPath", "0", "1", "-1", "1.5",
]


def _mutate(rng: random.Random, text: str) -> str:
    tokens = _TOKEN.findall(text)
    for _ in range(rng.randint(1, 3)):
        op = rng.random()
        k = rng.randrange(len(tokens) + 1)
        if op < 0.35 and tokens:
            del tokens[min(k, len(tokens) - 1)]
        elif op < 0.75:
            tokens.insert(k, rng.choice(_POOL))
        elif len(tokens) >= 2:
            i, j = rng.randrange(len(tokens)), rng.randrange(len(tokens))
            tokens[i], tokens[j] = tokens[j], tokens[i]
    return "".join(tokens)


def _check(text: str, params: dict) -> None:
    out, insertions = cypher_scope._scope_with_insertions(text, params, CALLER)
    assert isinstance(out, (Scoped, Refused)), text
    if isinstance(out, Scoped):
        assert out.parameters[SCOPE_PARAM] == [1, 2]
        rebuilt, last = [], 0
        for offset, piece in insertions:
            rebuilt += [text[last:offset], piece]
            last = offset
        rebuilt.append(text[last:])
        assert "".join(rebuilt) == out.cypher, text
    else:
        assert out.codes and len(out.reasons) >= len(out.codes)
        assert set(out.codes) <= set(cypher_scope.REFUSAL_CODES)


def test_mutants_never_raise():
    rng = random.Random(SEED)
    corpus = [(c.cypher, c.params) for c in TAUGHT + ACCEPTED] + [(r.cypher, {}) for r in REFUSALS
                                                                  if len(r.cypher) < 2000]
    seen = 0
    for text, params in corpus:
        for _ in range(MUTANTS_PER_STATEMENT):
            _check(_mutate(rng, text), params)
            seen += 1
    assert seen >= 4000


def test_random_noise_never_raises():
    rng = random.Random(SEED + 1)
    alphabet = "".join(chr(c) for c in range(32, 127)) + "\n\t\u00a0\u00e9\u2028"
    for _ in range(1500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        _check(text, {})


def test_deep_and_long_inputs_never_raise():
    for depth in (31, 32, 33, 200, 5000):
        _check("RETURN " + "(" * depth + "1" + ")" * depth + " AS x", {})
        _check("RETURN " + "[" * depth + "1" + "]" * depth + " AS x", {})
        _check("MATCH (s:Sample) WHERE " + "EXISTS { (s)-[:DERIVED_FROM]->(:Sample) WHERE " * depth
               + "true" + " }" * depth + " RETURN 1 AS x", {})
    _check("MATCH (s:Sample) RETURN " + " + ".join(["1"] * 5000) + " AS x", {})
    _check("MATCH (s:Sample) WHERE " + " ".join(["NOT"] * 3000) + " true RETURN 1 AS x", {})


def test_nesting_at_the_limit_is_accepted_and_past_it_refused():
    ok = scope_cypher("MATCH (s:Sample) RETURN " + "(" * 30 + "1" + ")" * 30 + " AS x", {}, CALLER)
    assert isinstance(ok, Scoped)
    deep = scope_cypher("MATCH (s:Sample) RETURN " + "(" * 33 + "1" + ")" * 33 + " AS x", {}, CALLER)
    assert isinstance(deep, Refused) and deep.codes == ("too_deep",)

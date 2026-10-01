"""
A seeded generator of read statements over the fixture schema, for the lane's differential oracle.

It composes a start (a typed or untyped sample, a fulltext search, a lineage hop or path, a study, investigation,
project or person pattern), an optional filter (properties, label tests, EXISTS and COUNT subqueries), an optional
second clause (OPTIONAL MATCH, a WITH chain) and a projection (counts, properties, whole nodes, paths, maps). About one
statement in ten uses a shape the prover refuses; the lane lets generator statements refuse freely and holds every
accepted one to the oracle. Nothing here orders by a column it then cuts with a small LIMIT, so the rows compare as
multisets.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 11.2.
"""
from __future__ import annotations

import random
from typing import NamedTuple

SEED = 71_2026
COUNT = 300

SAMPLE_LABELS = ["Sample", "T_MUS", "T_TIS", "T_SLD", "T_CHM"]
END_LABELS = ["", ":Sample", ":T_MUS", ":T_TIS", ":T_SLD", ":T_CHM"]
PARAMS = {"term": "mouse", "q": "alpha", "organ": "lung", "uid": "TIS-230302CCC-2", "lab": "AAA",
          "assay": "Staining", "project": "project", "study": "study", "min": 1.2}


class Generated(NamedTuple):
    id: str
    cypher: str
    params: dict


def _start(rng: random.Random) -> tuple[str, list[str], list[str], str]:
    """(clause, bound sample variables, other bound variables, kind)."""
    kind = rng.choice(["node", "node", "fulltext", "hop", "hop", "path", "path", "study", "study", "project",
                       "person", "investigation"])
    label = rng.choice(SAMPLE_LABELS)
    if kind == "node":
        return f"MATCH (a:{label})", ["a"], [], kind
    if kind == "fulltext":
        where = rng.choice(["", f" WHERE a:{label}"])
        return (f"CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS a, score{where}",
                ["a"], [], kind)
    if kind == "hop":
        arrow = rng.choice(["-[r:DERIVED_FROM]->", "<-[r:DERIVED_FROM]-", "-[r:DERIVED_FROM]-"])
        return f"MATCH (a:{label}){arrow}(b{rng.choice(END_LABELS)})", ["a", "b"], [], kind
    if kind == "path":
        k = rng.randint(1, 4)
        low = rng.choice(["0", "1"])
        arrow = rng.choice([f"-[:DERIVED_FROM*{low}..{k}]->", f"<-[:DERIVED_FROM*{low}..{k}]-",
                            f"-[:DERIVED_FROM*{low}..{k}]-"])
        return f"MATCH p = (a:{label}){arrow}(b{rng.choice(END_LABELS)})", ["a", "b"], [], kind
    if kind == "study":
        if rng.random() < 0.5:
            return f"MATCH (a:{label})-[:IN_STUDY]->(st:Study)", ["a"], ["st"], kind
        return (f"MATCH (a:{label})-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation)", ["a"],
                ["st", "inv"], kind)
    if kind == "project":
        return f"MATCH (a:{label})-[:IN_PROJECT]->(pr:Project)", ["a"], ["pr"], kind
    if kind == "person":
        return "MATCH (per:Person)-[:MEMBER_OF]->(pr:Project)", [], ["per", "pr"], kind
    return "MATCH (inv:Investigation)-[:IN_PROJECT]->(pr:Project)", [], ["inv", "pr"], kind


def _filter(rng: random.Random, var: str) -> str:
    return rng.choice([
        "",
        f"{var}.uuid STARTS WITH 'MUS'",
        f"toLower({var}.search_text) CONTAINS $term",
        f"{var}:T_TIS OR {var}:T_MUS",
        f"NOT {var}:T_SLD",
        f"EXISTS {{ ({var})-[:DERIVED_FROM*1..3]->(:{rng.choice(['Sample', 'T_MUS', 'T_CHM'])}) }}",
        f"NOT EXISTS {{ ({var})<-[:DERIVED_FROM]-(:Sample) }}",
        f"COUNT {{ ({var})-[:DERIVED_FROM]-() }} > 0",
        f"EXISTS {{ MATCH ({var})-[:IN_STUDY]->(fs:Study) WHERE toLower(fs.title) CONTAINS $study }}",
        f"{var}.Organ IS NOT NULL",
        f"toLower(toString({var}.Organ)) = $organ",
        f"{var}.Concentration > $min",
        f"any(x IN coalesce({var}.project_ids, []) WHERE x > 0)",
        f"{var}.uuid =~ ('(?i)^[^-]+-[0-9]{{6}}' + $lab + '-.*')",
        f"EXISTS {{ MATCH ({var})-[:IN_PROJECT]->(fp:Project) WHERE toLower(fp.title) CONTAINS $project }}",
    ])


def _second(rng: random.Random, kind: str) -> tuple[str, list[str], list[str]]:
    """(clause, new sample variables, other new variables)."""
    if kind in ("person", "investigation"):
        return rng.choice([
            ("", [], []),
            ("OPTIONAL MATCH (x:Sample)-[:IN_PROJECT]->(pr)", ["x"], []),
        ])
    return rng.choice([
        ("", [], []),
        ("", [], []),
        ("OPTIONAL MATCH (a)-[:DERIVED_FROM]->(c)", ["c"], []),
        ("OPTIONAL MATCH (a)<-[:DERIVED_FROM*1..3]-(c:Sample)", ["c"], []),
        ("OPTIONAL MATCH (a)-[:IN_STUDY]->(st2:Study)", [], ["st2"]),
        ("OPTIONAL MATCH (a)-[:IN_STUDY]->(st2:Study)-[:IN_INVESTIGATION]->(inv2:Investigation)", [],
         ["st2", "inv2"]),
        ("WITH a MATCH (a)<-[:DERIVED_FROM]-(c:Sample)", ["c"], []),
        ("WITH a MATCH (a)-[:IN_PROJECT]->(pr2:Project)", [], ["pr2"]),
        # Shapes the prover refuses.
        ("MATCH (a)-[:DERIVED_FROM]->(c)-->(d)", ["c"], []),
        ("MATCH (st3:Study)", [], ["st3"]),
        ("MATCH (a)-[:IN_STUDY]->(:Study)-[:IN_INVESTIGATION]->(:Investigation)<-[:IN_INVESTIGATION]-(st3:Study)",
         [], ["st3"]),
        ("MATCH (a)-[:OF_TYPE]->(t:SampleType)", [], ["t"]),
    ])


def _projection(rng: random.Random, samples: list[str], others: list[str], kind: str) -> str:
    choice = rng.random()
    if choice < 0.25 or not (samples or others):
        return "RETURN count(*) AS n"
    if choice < 0.35 and samples:
        return f"RETURN count(DISTINCT {samples[0]}) AS n"
    if choice < 0.45 and samples:
        return f"RETURN {samples[0]}.type AS type, count(*) AS n"
    items: list[str] = []
    for var in samples:
        items.append(rng.choice([f"{var}.uuid AS {var}_uuid", f"{var}.title AS {var}_title",
                                 f"{var} AS {var}_node", f"{var} {{.uuid, .Strain, .Organ}} AS {var}_map",
                                 f"labels({var}) AS {var}_labels", f"{var}.search_text AS {var}_text",
                                 f"{var}.project_ids AS {var}_projects"]))
    for var in others:
        items.append(rng.choice([f"{var}.title AS {var}_title", f"{var} AS {var}_node", f"{var}.id AS {var}_id"]))
    if kind == "path":
        items.append(rng.choice(["length(p) AS hops", "[n IN nodes(p) | n.title] AS chain", "p AS path",
                                 "[r IN relationships(p) | r.protocol_title] AS protocols"]))
    if kind == "hop":
        items.append(rng.choice(["r.protocol_title AS protocol", "r.internal_assay_title AS assay", "r AS rel"]))
    if kind == "fulltext":
        items.append("score")
    distinct = rng.choice(["", "DISTINCT "])
    return f"RETURN {distinct}" + ", ".join(dict.fromkeys(items))


def generate(seed: int = SEED, count: int = COUNT) -> list[Generated]:
    rng = random.Random(seed)
    out: list[Generated] = []
    seen: set[str] = set()
    while len(out) < count:
        start, samples, others, kind = _start(rng)
        where = _filter(rng, samples[0]) if samples else ""
        text = start
        if where:
            text += (" AND " if " WHERE " in start else "\nWHERE ") + where
        second, new_samples, new_others = _second(rng, kind)
        if second:
            text += "\n" + second
            if second.startswith("WITH"):
                samples, others, kind = ["a"], [], "with"
        text += "\n" + _projection(rng, samples + new_samples, others + new_others, kind)
        if text in seen:
            continue
        seen.add(text)
        out.append(Generated(f"gen{len(out):03d}", text, dict(PARAMS)))
    return out

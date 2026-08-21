"""How sample types feed one another, and how far down the pipeline each sits.

Pure functions only -- no openpyxl, no ORM, no Django settings. The workbook
writer owns every Excel concern; this module owns the graph.
"""

import re
from collections.abc import Mapping

# Sample UIDs lead with the sample-type code: "MUS-230101ABC-1", "D.SEQ-240910LAU-3".
# The dotted alternative must come first or "D.SEQ" truncates to "D".
SAMPLE_TYPE_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"


def derivation_edges(df, assay_by_uuid: Mapping[str, str],
                     hops: list[tuple[str, str, str]] | None = None
                     ) -> dict[tuple[str, str], set[str]]:
    """(parent type, child type) -> the assay titles seen on that hop.

    Hops come from Neo4j when it is reachable, where the assay is recorded on
    the relationship itself. Otherwise they are recovered from each row's own
    Parent UIDs, whose prefix is the parent's sample type -- the same hops,
    labelled from the weaker per-sample assay link or not at all.

    Upstream types are included even when they were not downloaded: that is the
    part a reader cannot otherwise see.
    """
    edges: dict[tuple[str, str], set[str]] = {}

    for parent_uuid, assay, child_uuid in hops or []:
        parent_match = re.match(SAMPLE_TYPE_RE, str(parent_uuid))
        child_match = re.match(SAMPLE_TYPE_RE, str(child_uuid))
        if not parent_match or not child_match:
            continue
        parent_type, child_type = parent_match.group(1), child_match.group(1)
        if parent_type == child_type:
            continue
        edges.setdefault((parent_type, child_type), set())
        if assay:
            edges[(parent_type, child_type)].add(assay)
    if edges:
        return edges

    if "Parent" not in df.columns:
        return {}
    for uuid, child_type, parents in zip(df["uuid"], df["sample_type"], df["Parent"]):
        # isinstance, not just falsiness: `sample_type` is a str.extract result,
        # so a UID carrying no [A-Z] run yields NaN -- a float, and `not NaN` is
        # False. Such a type used to seed an edge keyed on a float, which
        # sample_type_depths then tried to sort against strings, taking the
        # whole download down with a TypeError. The parent side needs no such
        # guard: it is always re.match(...).group(1), i.e. always a str.
        if not isinstance(child_type, str) or not child_type or parents is None:
            continue
        text = str(parents).strip()
        if not text or text.lower() in ("nan", "none"):
            continue
        assay = assay_by_uuid.get(str(uuid), "")
        for token in re.split(r"[;,]", text):
            match = re.match(SAMPLE_TYPE_RE, token.strip())
            if not match or match.group(1) == child_type:
                continue
            edges.setdefault((match.group(1), child_type), set())
            if assay:
                edges[(match.group(1), child_type)].add(assay)

    return edges


def sample_type_depths(edges: Mapping[tuple[str, str], set[str]]) -> dict[str, int]:
    """Each type -> its longest distance from a type with no parent.

    This is the definition of "generation order" used for both the flow sheet
    and the sheet order. Longest, not shortest: a type reachable both directly
    and through a chain belongs at the later point, where a reader expects it.

    Cycle-safe -- the production graph really does contain CEL <-> D.FLOW,
    TIS <-> BAC, TIS <-> D.BSRA, CEL <-> OOC and A.IMG <-> MDL. A type already
    on the current recursion stack contributes 0 rather than recursing. Nodes
    are visited in sorted order so a cycle resolves identically on every run.
    """
    parents: dict[str, list[str]] = {}
    for parent, child in edges:
        parents.setdefault(parent, [])
        parents.setdefault(child, []).append(parent)

    depths: dict[str, int] = {}

    def depth(node: str, stack: frozenset) -> int:
        if node in depths:
            return depths[node]
        if node in stack:
            return 0
        upstream = [depth(p, stack | {node}) for p in sorted(parents[node])]
        depths[node] = max(upstream) + 1 if upstream else 0
        return depths[node]

    for node in sorted(parents):
        depth(node, frozenset())
    return depths


def _arrow(assays: set[str]) -> str:
    labels = sorted(a for a in assays if a)
    return f"--[{', '.join(labels)}]-->" if labels else "------>"


def build_provenance_rows(edges: Mapping[tuple[str, str], set[str]],
                          depths: Mapping[str, int]) -> list[list[str]]:
    """One row per chain, as alternating cells: type, arrow, type, arrow, type.

    A *fresh-hop path cover*: take the earliest uncovered hop, extend it
    forward and backward through hops not yet used, emit, repeat. Every hop
    appears in exactly one row, so nothing is lost and nothing is duplicated.

    Measured on the full local graph: 129 hops become 71 chains, widest 13
    cells. The obvious alternative -- root-anchored paths, longest first --
    produced 89 chains that re-tread the same prefixes and still missed the two
    hops reachable only through a cycle.

    Chains that start mid-pipeline are expected: their upstream was already
    shown by an earlier row.
    """
    children: dict[str, list[str]] = {}
    parents: dict[str, list[str]] = {}
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
        parents.setdefault(child, []).append(parent)
        children.setdefault(child, [])
        parents.setdefault(parent, [])

    def rank(node: str) -> tuple:
        return (depths.get(node, len(depths)), node)

    free = set(edges)
    chains: list[list[str]] = []
    while free:
        parent, child = min(free, key=lambda e: (rank(e[0]), e[1]))
        path = [parent, child]
        free.discard((parent, child))

        while True:
            forward = [(path[-1], c) for c in children[path[-1]]
                       if (path[-1], c) in free and c not in path]
            if not forward:
                break
            step = min(forward, key=lambda e: rank(e[1]))
            path.append(step[1])
            free.discard(step)

        while True:
            backward = [(p, path[0]) for p in parents[path[0]]
                        if (p, path[0]) in free and p not in path]
            if not backward:
                break
            step = max(backward, key=lambda e: rank(e[0]))
            path.insert(0, step[0])
            free.discard(step)

        chains.append(path)

    chains.sort(key=lambda path: rank(path[0]))

    rows = []
    for path in chains:
        row = [path[0]]
        for parent, child in zip(path, path[1:]):
            row.append(_arrow(edges[(parent, child)]))
            row.append(child)
        rows.append(row)
    return rows

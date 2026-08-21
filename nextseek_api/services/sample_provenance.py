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
        if not child_type or parents is None:
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

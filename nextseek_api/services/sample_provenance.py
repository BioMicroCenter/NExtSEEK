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


def build_provenance_tree(edges: Mapping[tuple[str, str], set[str]]) -> list[str]:
    """One indented line per node, as an ASCII tree rooted at each origin type.

    Replaces the flat-chain cover. That cover spent every hop on exactly one
    row, so a chain arriving at a type whose onward hop was already used simply
    stopped -- fifteen rows began bare at TIS with no way to see where TIS came
    from. Re-treading a shared prefix is how a person reads lineage.

    Two guards stop the walk and they are not interchangeable:

    `(cycle)` fires when a type is already on the CURRENT path. The production
    graph really does contain CEL <-> D.FLOW, TIS <-> BAC, TIS <-> D.BSRA,
    CEL <-> OOC and A.IMG <-> MDL; without this the walk does not terminate.

    `(expanded above)` fires when a type's subtree was already drawn ANYWHERE.
    The graph is a DAG, not a tree -- TIS has six parents and D.IMG has
    seventeen -- so expanding every occurrence yields 3,763 lines against the
    150-hop production graph, nearly all of it the same subtrees repeated. With
    the guard it is 178. A type with no children is drawn in full instead: the
    marker would be noise where there is nothing to expand.

    Roots and siblings sort by name, so the tree is stable across runs. It takes
    no `depths` argument: every root is depth 0 by definition, so depth-sorting
    them would be a no-op. The caller still computes depths -- the sheet ORDER
    uses them -- but this function does not.

    A root is normally "a type no edge names as a child." That definition
    misses a strongly-connected cluster with no external entry point -- e.g.
    `{("X", "X"): set()}` or `{("A", "B"): set(), ("B", "A"): set()}` -- where
    every member has a parent, so none is eligible and the whole cluster would
    silently vanish. After the normal roots are walked, a second pass scans
    every node in sorted order and walks any that is still unexpanded as its
    own root. Walking a node marks its whole forward-reachable component
    expanded, so this naturally lands on just the lowest-sorted unexpanded
    node of each remaining component, one extra tree per component, still
    blank-line separated and still deterministic.
    """
    children: dict[str, list[tuple[str, list[str]]]] = {}
    have_parent: set[str] = set()
    for (parent, child), assays in edges.items():
        children.setdefault(parent, []).append(
            (child, sorted(assay for assay in assays if assay)))
        children.setdefault(child, [])
        have_parent.add(child)

    lines: list[str] = []
    expanded: set[str] = set()

    def walk(node: str, assays: list[str], path: frozenset,
             prefix: str, connector: str) -> None:
        if node in path:
            lines.append(f"{prefix}{connector}{node}   (cycle)")
            return
        if node in expanded and children.get(node):
            lines.append(f"{prefix}{connector}{node}   (expanded above)")
            return
        expanded.add(node)
        label = f"{node}   [{', '.join(assays)}]" if assays else node
        lines.append(f"{prefix}{connector}{label}")

        kids = sorted(children.get(node, []))
        for index, (child, child_assays) in enumerate(kids):
            last = index == len(kids) - 1
            # The root's own line carries no connector, so its children hang
            # directly off column zero rather than being pushed right by it.
            extension = "" if not connector else ("    " if connector.startswith("└") else "│   ")
            walk(child, child_assays, path | {node},
                 prefix + extension, "└── " if last else "├── ")

    for index, root in enumerate(sorted(n for n in children if n not in have_parent)):
        if index:
            lines.append("")
        walk(root, [], frozenset(), "", "")

    # Second pass: any node still unexpanded belongs to a rootless component
    # (every member has a parent, so the loop above never reached it). Walking
    # the lowest-sorted unexpanded node marks its whole forward-reachable
    # component expanded, so this loop naturally picks exactly one root per
    # remaining component without tracking components explicitly.
    for node in sorted(children):
        if node in expanded:
            continue
        if lines:
            lines.append("")
        walk(node, [], frozenset(), "", "")
    return lines

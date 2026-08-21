# Download provenance order, tab order and house vocabularies — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the downloaded workbook order itself by how samples were actually generated, keep dropdowns alive past the filled rows, and offer NExtSEEK's own house terms instead of GEO's.

**Architecture:** The provenance logic — edge building, depth, and the chain cover — moves out of `sample_workbook.py` into a new pure-Python `sample_provenance.py` with no openpyxl and no Django ORM, so it is testable without a workbook. `sample_workbook.py` keeps all Excel concerns: it calls the three provenance functions, sorts its sheets by the depths they return, writes the chains onto a dedicated sheet, and widens the dropdown ranges.

**Tech Stack:** Python 3, pandas, openpyxl, pytest. Tests run in the stack image via `./scripts/run_tests.sh` (mysqlclient does not build on a bare macOS host).

**Spec:** `docs/2026-08-21-download-provenance-order-and-house-vocabularies-design.md`

## Global Constraints

- Work in the `/Users/jps/Documents/MIT/NExtSEEK-readme-columns` worktree, on branch `feat/download-readme-columns`. Do not touch the main checkout.
- Run tests with `./scripts/run_tests.sh <target>`, never bare `pytest` and never `uv run pytest` on the host.
- Every lookup that leaves the process (Neo4j, the ORM) must fail soft: losing it costs the feature, never the download. Follow the existing `try/except` + `logger.exception` pattern in `sample_workbook.py`.
- `DataValidation.errorStyle` stays `"warning"`. Never `"stop"` — thousands of production rows hold values the house lists exclude, and a hard reject fires on open for rows the researcher never touched.
- All text written into a cell goes through `_safe_cell_value`, via `_write_cell` where one exists.
- Sample-type codes are matched with `SAMPLE_TYPE_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"`. The dotted alternative must stay first or `D.SEQ` truncates to `D`. This regex lives in exactly one place.
- Conventional commits with module scopes, e.g. `feat(download): …`, `refactor(download): …`.
- Do not modify `sample_fields_context`, the three production SQL scripts in `startup/seed/sql/`, or anything to do with attribute definitions.

## File Structure

| File | Responsibility |
|---|---|
| `nextseek_api/services/sample_provenance.py` | **New.** Pure functions: `SAMPLE_TYPE_RE`, `derivation_edges`, `sample_type_depths`, `build_provenance_rows`. No openpyxl, no ORM, no Django settings. |
| `nextseek_api/tests/test_sample_provenance.py` | **New.** Unit tests for the above. No workbook, no `tmp_path`. |
| `nextseek_api/services/sample_workbook.py` | **Modify.** Imports `SAMPLE_TYPE_RE` and the three functions; adds `_write_flow_sheet`; sorts `prepared` by depth; widens dropdown ranges. Loses `build_provenance_lines` and `_format_provenance`. |
| `nextseek_api/tests/test_sample_workbook.py` | **Modify.** The nine `build_provenance_lines` tests migrate to the new module or to sheet-level assertions; new tests for sheet order and dropdown extent. |
| `nextseek_api/services/controlled_vocabularies.json` | **Rewrite.** GEO terms → NExtSEEK house terms, plus a `variants` documentation block. |

---

### Task 1: Provenance edges and depth

Extract the edge-building half of `build_provenance_lines` into its own module, and add the depth calculation the ordering depends on.

**Files:**
- Create: `nextseek_api/services/sample_provenance.py`
- Create: `nextseek_api/tests/test_sample_provenance.py`
- Modify: `nextseek_api/services/sample_workbook.py` — `build_provenance_lines` loses its duplicated body and delegates to `derivation_edges`

**This task must not leave the same logic in two places.** The extraction is only half-done if `build_provenance_lines` keeps its own copy of the edge-building loop. Step 3b below rewrites it to delegate. Task 3 then deletes the thin wrapper that remains.

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `SAMPLE_TYPE_RE: str`
  - `derivation_edges(df, assay_by_uuid: Mapping[str, str], hops: list[tuple[str, str, str]] | None = None) -> dict[tuple[str, str], set[str]]` — maps `(parent_type, child_type)` to the set of assay titles seen on that hop. Empty strings are never added to the set.
  - `sample_type_depths(edges: Mapping[tuple[str, str], set[str]]) -> dict[str, int]` — every type appearing in `edges`, mapped to its longest distance from a type with no parent. Types absent from `edges` are absent from the result; callers use `.get(code, math.inf)`.

- [ ] **Step 1: Write the failing tests**

Create `nextseek_api/tests/test_sample_provenance.py`:

```python
"""Provenance: which types feed which, and how far down the pipeline each sits."""

import math

import pandas as pd

from nextseek_api.services.sample_provenance import (
    derivation_edges,
    sample_type_depths,
)


def _prov_df():
    """Two hops, one of them from a type not itself downloaded."""
    return pd.DataFrame([
        {"uuid": "D.SEQ-1", "sample_type": "D.SEQ", "Parent": "DNA-9"},
        {"uuid": "TIS-2", "sample_type": "TIS", "Parent": "MUS-3; MUS-4"},
    ])


def test_edges_take_the_assay_from_the_neo4j_hop():
    hops = [("DNA-9", "Short Read Sequencing", "D.SEQ-1")]
    edges = derivation_edges(_prov_df(), {}, hops)
    assert edges[("DNA", "D.SEQ")] == {"Short Read Sequencing"}


def test_neo4j_hops_win_over_the_parent_column():
    """Both describe the same lineage, but only the graph edge knows which
    assay produced this particular child."""
    hops = [("DNA-9", "Short Read Sequencing", "D.SEQ-1")]
    edges = derivation_edges(_prov_df(), {"D.SEQ-1": "Something Else"}, hops)
    assert edges == {("DNA", "D.SEQ"): {"Short Read Sequencing"}}


def test_edges_fall_back_to_the_parent_column_without_hops():
    """An unreachable graph costs the assay labels, not the lineage."""
    edges = derivation_edges(_prov_df(), {}, [])
    assert edges[("DNA", "D.SEQ")] == set()
    assert ("MUS", "TIS") in edges


def test_edges_collapse_repeated_parents_into_one_hop():
    """TIS has two MUS parents; that is one relationship, not two."""
    edges = derivation_edges(_prov_df(), {})
    assert len([e for e in edges if e[0] == "MUS"]) == 1


def test_edges_ignore_a_parent_of_the_same_type():
    df = pd.DataFrame([{"uuid": "CEL-1", "sample_type": "CEL", "Parent": "CEL-0"}])
    assert derivation_edges(df, {}) == {}


def test_edges_are_empty_without_a_parent_column():
    df = pd.DataFrame([{"uuid": "MUS-1", "sample_type": "MUS", "Name": "m1"}])
    assert derivation_edges(df, {}) == {}


def test_dotted_type_codes_survive_extraction():
    """D.SEQ must not truncate to D."""
    hops = [("DNA-9", "", "D.SEQ-1")]
    assert ("DNA", "D.SEQ") in derivation_edges(_prov_df(), {}, hops)


def test_depth_counts_from_a_type_with_no_parent():
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set()}
    assert sample_type_depths(edges) == {"PAT": 0, "PAV": 1, "TIS": 2, "DNA": 3}


def test_depth_is_the_longest_path_not_the_shortest():
    """DNA is reachable from PAT in one hop and in three. It sits at three:
    a reader wants the latest point at which the type can appear."""
    edges = {("PAT", "DNA"): set(), ("PAT", "PAV"): set(),
             ("PAV", "TIS"): set(), ("TIS", "DNA"): set()}
    assert sample_type_depths(edges)["DNA"] == 3


def test_depth_terminates_on_a_two_cycle():
    """CEL <-> D.FLOW is real in production. Depth must not recurse forever."""
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    depths = sample_type_depths(edges)
    assert set(depths) == {"TIS", "CEL", "D.FLOW"}
    assert all(isinstance(d, int) for d in depths.values())


def test_depth_resolves_a_cycle_the_same_way_every_run():
    """Without a fixed visit order, which member of a cycle gets depth 0
    would depend on dict iteration."""
    edges = {("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    results = [sample_type_depths(edges) for _ in range(5)]
    assert all(r == results[0] for r in results)


def test_depth_omits_types_that_appear_in_no_edge():
    edges = {("PAT", "PAV"): set()}
    assert "MUS" not in sample_type_depths(edges)
    assert sample_type_depths(edges).get("MUS", math.inf) == math.inf
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'nextseek_api.services.sample_provenance'`.

- [ ] **Step 3: Write the implementation**

Create `nextseek_api/services/sample_provenance.py`:

```python
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
```

- [ ] **Step 3b: Delete the duplicate from `sample_workbook.py`**

The new module now owns the edge-building loop, so `build_provenance_lines` must stop carrying its own copy. In `nextseek_api/services/sample_workbook.py`:

Add the import beside the other imports:

```python
from nextseek_api.services.sample_provenance import SAMPLE_TYPE_RE, derivation_edges
```

Delete the local `SAMPLE_TYPE_RE` assignment and its two comment lines — it now comes from the import, and it must live in exactly one place.

Replace the whole body of `build_provenance_lines` (everything after its docstring) with a delegation, leaving `_format_provenance` untouched:

```python
    return _format_provenance(derivation_edges(df, assay_by_uuid, hops))
```

The function keeps its signature and its nine existing tests in `test_sample_workbook.py`, which must all still pass — that is the evidence the extraction preserved behaviour. Task 3 deletes the wrapper once nothing needs it.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py nextseek_api/tests/test_sample_workbook.py -v
```

Expected: 12 passed in `test_sample_provenance.py`, and **every pre-existing test in `test_sample_workbook.py` still passing** — including all nine `build_provenance_lines` tests. A failure there means the extraction changed behaviour; fix the extraction, do not edit those tests.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_provenance.py nextseek_api/tests/test_sample_provenance.py nextseek_api/services/sample_workbook.py
git commit -m "refactor(download): provenance edges and depth in their own module"
```

---

### Task 2: The chain cover

Turn the edge set into rows that read left to right, each hop appearing exactly once.

**Files:**
- Modify: `nextseek_api/services/sample_provenance.py`
- Modify: `nextseek_api/tests/test_sample_provenance.py`

**Interfaces:**
- Consumes: `derivation_edges`, `sample_type_depths` from Task 1.
- Produces: `build_provenance_rows(edges: Mapping[tuple[str, str], set[str]], depths: Mapping[str, int]) -> list[list[str]]` — each inner list is the alternating cells of one chain, ready to write: type, arrow, type, arrow, type… Arrow cells are `--[Assay]-->`, or `------>` when the hop carries no assay. Several assays join with `, `, sorted.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_provenance.py`:

```python
from nextseek_api.services.sample_provenance import build_provenance_rows


def _rows(edges):
    return build_provenance_rows(edges, sample_type_depths(edges))


def test_a_chain_reads_left_to_right_across_the_row():
    edges = {("PAT", "PAV"): {"Consent"}, ("PAV", "TIS"): {"Tissue Collection"}}
    assert _rows(edges) == [
        ["PAT", "--[Consent]-->", "PAV", "--[Tissue Collection]-->", "TIS"],
    ]


def test_a_hop_without_an_assay_gets_a_plain_arrow():
    assert _rows({("DNA", "D.SEQ"): set()}) == [["DNA", "------>", "D.SEQ"]]


def test_several_assays_on_one_hop_join_sorted():
    edges = {("TIS", "D.IMG"): {"Imaging", "Histology"}}
    assert _rows(edges)[0][1] == "--[Histology, Imaging]-->"


def test_every_hop_appears_exactly_once():
    """The whole point of a cover: no hop repeated, none dropped."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set(),
             ("TIS", "D.IMG"): set(), ("DNA", "D.SEQ"): set()}
    seen = []
    for row in _rows(edges):
        types = row[::2]
        seen += list(zip(types, types[1:]))
    assert sorted(seen) == sorted(edges)


def test_chains_sort_by_the_depth_of_their_first_type():
    """Earliest-generated flows come first -- that is the ordering asked for."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "D.IMG"): set(),
             ("D.IMG", "A.MIGR"): set(), ("TIS", "D.TITR"): set()}
    first_types = [row[0] for row in _rows(edges)]
    depths = sample_type_depths(edges)
    assert first_types == sorted(first_types, key=lambda t: (depths[t], t))
    assert first_types[0] == "PAT"


def test_a_chain_may_start_mid_pipeline():
    """A hop whose upstream was already shown starts its own chain rather than
    re-treading the prefix."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(),
             ("TIS", "DNA"): set(), ("TIS", "D.IMG"): set()}
    rows = _rows(edges)
    assert len(rows) == 2
    assert rows[0][0] == "PAT"
    assert rows[1][0] == "TIS"


def test_a_cycle_terminates_and_visits_each_type_once_per_chain():
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    rows = _rows(edges)
    for row in rows:
        types = row[::2]
        assert len(types) == len(set(types))
    seen = []
    for row in rows:
        types = row[::2]
        seen += list(zip(types, types[1:]))
    assert sorted(seen) == sorted(edges)


def test_no_edges_yields_no_rows():
    assert build_provenance_rows({}, {}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py -v
```

Expected: collection error — `ImportError: cannot import name 'build_provenance_rows'`.

- [ ] **Step 3: Write the implementation**

Append to `nextseek_api/services/sample_provenance.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py -v
```

Expected: 20 passed.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_provenance.py nextseek_api/tests/test_sample_provenance.py
git commit -m "feat(download): chains that cover every hop exactly once"
```

---

### Task 3: The flow sheet

Move the provenance out of the README and onto its own sheet, where it can have its own column widths.

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Modify: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `derivation_edges`, `sample_type_depths`, `build_provenance_rows`, `SAMPLE_TYPE_RE` from Tasks 1–2.
- Produces: `FLOW_SHEET = "How this data flowed"`; `_write_flow_sheet(book, rows: list[list[str]]) -> None`, which creates the sheet at index 1 and returns `None` without creating anything when `rows` is empty.

- [ ] **Step 1: Write the failing tests**

In `nextseek_api/tests/test_sample_workbook.py`, **delete** these nine now-superseded tests, whose behaviours moved to `test_sample_provenance.py` in Tasks 1–2:

`test_provenance_names_the_assay_recorded_on_the_neo4j_edge`,
`test_neo4j_hops_win_over_the_parent_column`,
`test_provenance_falls_back_to_the_parent_column_without_neo4j`,
`test_provenance_names_the_assay_that_produced_the_child`,
`test_provenance_falls_back_to_a_plain_arrow_without_an_assay`,
`test_provenance_collapses_repeated_parents_into_one_hop`,
`test_provenance_includes_types_that_were_not_downloaded`,
`test_provenance_ignores_a_parent_of_the_same_type`,
`test_provenance_is_empty_without_a_parent_column`.

Keep `_prov_df`, `test_lineage_lookup_survives_an_unreachable_graph` and `test_assay_lookup_survives_a_database_failure` — those test the lookups, which stay in `sample_workbook.py`.

Change the import block at the top of the file from `build_provenance_lines` to `FLOW_SHEET`:

```python
from nextseek_api.services.sample_workbook import (
    COLUMN_TABLE_HEADER,
    CONTEXTDB_URL,
    EXCEL_MAX_CELL_CHARS,
    CV_SHEET,
    FLOW_SHEET,
    SUMMARY_HEADER,
    build_readme_blocks,
    load_assay_titles,
    load_derivation_hops,
    load_sample_field_context,
    load_sample_type_context,
    write_samples_workbook,
)
```

Then append these tests:

```python
def _flow_df():
    """PAT -> PAV -> TIS, with TIS also downloaded."""
    return pd.DataFrame([
        {"uuid": "PAV-1", "sample_type": "PAV", "Parent": "PAT-9"},
        {"uuid": "TIS-2", "sample_type": "TIS", "Parent": "PAV-1"},
    ])


def _flow_rows(ws):
    return [[c.value for c in row if c.value is not None] for row in ws.iter_rows()
            if any(c.value is not None for c in row)]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_flow_sheet_sits_directly_after_the_readme(_ctx, _fields, _assays, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    assert load_workbook(out).sheetnames[:2] == ["README", FLOW_SHEET]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a_chain_occupies_one_row_across_columns(_ctx, _fields, _assays, tmp_path):
    """The whole reason for a separate sheet: one flow reads left to right."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    rows = _flow_rows(load_workbook(out)[FLOW_SHEET])
    assert ["PAT", "------>", "PAV", "------>", "TIS"] in rows


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_no_flow_sheet_when_there_is_no_lineage(_ctx, _fields, _assays, tmp_path):
    """An empty sheet reads as a rendering bug."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert FLOW_SHEET not in load_workbook(out).sheetnames


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_readme_points_at_the_flow_sheet(_ctx, _fields, _assays, tmp_path):
    """A reader who never opens the tab must still learn it is there."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    text = " ".join(str(c.value) for row in load_workbook(out)["README"].iter_rows()
                    for c in row if c.value)
    assert FLOW_SHEET in text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v
```

Expected: collection error — `ImportError: cannot import name 'FLOW_SHEET'`.

- [ ] **Step 3: Write the implementation**

In `nextseek_api/services/sample_workbook.py`:

**3a.** Task 1 already replaced the local `SAMPLE_TYPE_RE` with an import of `SAMPLE_TYPE_RE` and `derivation_edges`. Widen that existing import to cover the two functions this task needs:

```python
from nextseek_api.services.sample_provenance import (
    SAMPLE_TYPE_RE,
    build_provenance_rows,
    derivation_edges,
    sample_type_depths,
)
```

**3b.** Replace the `PROVENANCE_TITLE` constant with the flow-sheet constants:

```python
FLOW_SHEET = "How this data flowed"
FLOW_README_POINTER = f"How this data flowed: see the '{FLOW_SHEET}' sheet."
# Sample-type codes are short; assay titles are not. Alternating widths keep a
# chain readable without a 100-wide gap at every second type.
FLOW_TYPE_WIDTH = 14
FLOW_ARROW_WIDTH = 34
```

**3c.** Delete `build_provenance_lines` — after Task 1 it is a one-line wrapper — and `_format_provenance` with it. The edge building already lives in `sample_provenance.derivation_edges`; the one-line-per-hop formatting these two produced is replaced by the chains from `build_provenance_rows`.

**3d.** Add the sheet writer, directly after `_write_readme`:

```python
def _write_flow_sheet(book, rows: list[list[str]]) -> None:
    """One chain per row, alternating type and arrow cells.

    Its own sheet rather than a README section: README's columns are sized 46 /
    34 / 100 for the summary and column tables, which puts a 100-wide gap at
    every second type of a chain.
    """
    if not rows:
        return
    ws = book.create_sheet(FLOW_SHEET, 1)
    for index, row in enumerate(rows, start=1):
        for column, value in enumerate(row, start=1):
            _write_cell(ws, index, column, value, bold=(column % 2 == 1))
    widest = max(len(row) for row in rows)
    for column in range(1, widest + 1):
        ws.column_dimensions[get_column_letter(column)].width = (
            FLOW_TYPE_WIDTH if column % 2 else FLOW_ARROW_WIDTH
        )
```

**3e.** Change `_write_readme` to take a pointer flag instead of the provenance lines. Replace its signature and the whole `if provenance:` block:

```python
def _write_readme(book, blocks: list[dict], has_flow_sheet: bool = False) -> None:
```

```python
    # The flow lives on its own sheet; the README says so, because a reader who
    # never opens the tab must still learn it is there.
    if has_flow_sheet:
        _write_cell(ws, row, 1, FLOW_README_POINTER, bold=True)
        row += 2
```

**3f.** In `write_samples_workbook`, replace the three provenance lines

```python
    uuids = df["uuid"].astype(str)
    hops = load_derivation_hops(uuids)
    provenance = build_provenance_lines(
        df, {} if hops else load_assay_titles(uuids), hops
    )
```

with

```python
    uuids = df["uuid"].astype(str)
    hops = load_derivation_hops(uuids)
    edges = derivation_edges(df, {} if hops else load_assay_titles(uuids), hops)
    flow_rows = build_provenance_rows(edges, sample_type_depths(edges))
```

and replace the `_write_readme(book, blocks, provenance)` call with

```python
        _write_readme(book, blocks, bool(flow_rows))
        _write_flow_sheet(book, flow_rows)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v
```

Expected: all pass. The suite should be 4 tests larger and 9 smaller than before.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): the flow gets its own sheet, and reads left to right"
```

---

### Task 4: Sheets and README sections follow provenance order

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Modify: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `sample_type_depths`, `derivation_edges` from Task 1; the `edges` local introduced in Task 3.
- Produces: no new public names. `write_samples_workbook` sorts `prepared` by `(depth, code)` before building `sheets`.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
def _order_df():
    """Alphabetical order is DNA, PAT, PAV, TIS. Generation order is the
    reverse of that for PAT/PAV and puts DNA last."""
    return pd.DataFrame([
        {"uuid": "TIS-1", "sample_type": "TIS", "Parent": "PAV-1"},
        {"uuid": "DNA-1", "sample_type": "DNA", "Parent": "TIS-1"},
        {"uuid": "PAT-1", "sample_type": "PAT", "Parent": ""},
        {"uuid": "PAV-1", "sample_type": "PAV", "Parent": "PAT-1"},
    ])


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_sample_tabs_follow_generation_order(_ctx, _fields, _assays, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_order_df(), str(out))
    tabs = [n for n in load_workbook(out).sheetnames
            if n not in ("README", FLOW_SHEET, CV_SHEET)]
    assert tabs == ["PAT", "PAV", "TIS", "DNA"]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_the_readme_summary_follows_the_same_order(_ctx, _fields, _assays, tmp_path):
    """A workbook whose tabs and README disagree reads as a bug."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_order_df(), str(out))
    ws = load_workbook(out)["README"]
    codes = [ws.cell(row=r, column=1).value for r in range(4, 8)]
    assert codes == ["PAT", "PAV", "TIS", "DNA"]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_a_type_with_no_lineage_sorts_last(_ctx, _fields, _assays, tmp_path):
    """ABC has no hop at all. It cannot be placed in the pipeline, so it goes
    after everything that can be."""
    df = pd.DataFrame([
        {"uuid": "TIS-1", "sample_type": "TIS", "Parent": "PAV-1"},
        {"uuid": "PAV-1", "sample_type": "PAV", "Parent": ""},
        {"uuid": "ABC-1", "sample_type": "ABC", "Parent": ""},
    ])
    out = tmp_path / "w.xlsx"
    write_samples_workbook(df, str(out))
    tabs = [n for n in load_workbook(out).sheetnames
            if n not in ("README", FLOW_SHEET, CV_SHEET)]
    assert tabs == ["PAV", "TIS", "ABC"]


@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_order_stays_alphabetical_without_any_lineage(_ctx, _fields, _assays, tmp_path):
    """No graph and no usable Parent column: fall back to what it did before."""
    df = pd.DataFrame([
        {"uuid": "TIS-1", "sample_type": "TIS", "Name": "t"},
        {"uuid": "DNA-1", "sample_type": "DNA", "Name": "d"},
    ])
    out = tmp_path / "w.xlsx"
    write_samples_workbook(df, str(out))
    tabs = [n for n in load_workbook(out).sheetnames
            if n not in ("README", FLOW_SHEET, CV_SHEET)]
    assert tabs == ["DNA", "TIS"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -k "generation_order or same_order or sorts_last or stays_alphabetical" -v
```

Expected: `test_sample_tabs_follow_generation_order` and `test_the_readme_summary_follows_the_same_order` FAIL with tabs in alphabetical order `['DNA', 'PAT', 'PAV', 'TIS']`. The other two pass already — they pin behaviour that must survive.

- [ ] **Step 3: Write the implementation**

In `nextseek_api/services/sample_workbook.py`, add `import math` to the imports.

Move the lineage block **above** sheet preparation. The body of `write_samples_workbook` from `codes = …` to `blocks = …` becomes:

```python
    codes = df["sample_type"].dropna().unique().tolist()
    if context_by_code is None:
        context_by_code = load_sample_type_context(codes)

    # Lineage is loaded before the sheets are prepared, because the sheet order
    # is derived from it. Same single bounded query, just earlier.
    uuids = df["uuid"].astype(str)
    hops = load_derivation_hops(uuids)
    edges = derivation_edges(df, {} if hops else load_assay_titles(uuids), hops)
    depths = sample_type_depths(edges)
    flow_rows = build_provenance_rows(edges, depths)

    prepared = []
    for sample_type, sample_type_df in df.groupby("sample_type"):
        frame = sample_type_df.drop(columns=["uuid", "sample_type"])
        frame = frame.replace("", pd.NA)
        frame = frame.dropna(axis=1, how="all")
        prepared.append((sample_type, frame))

    # Generation order, not alphabetical: a reader meets the sample types in
    # the order they were made. A type with no hop at all cannot be placed in
    # the pipeline, so it sorts after everything that can be. No lineage at all
    # leaves every depth infinite, which is a stable alphabetical sort.
    prepared.sort(key=lambda item: (depths.get(item[0], math.inf), item[0]))

    sheets = [(code, list(frame.columns)) for code, frame in prepared]
    meaning_by_pair = load_sample_field_context(
        [(code, column) for code, columns in sheets for column in columns]
    )
    blocks = build_readme_blocks(sheets, context_by_code, meaning_by_pair)
```

Delete the now-duplicated `uuids` / `hops` / `edges` / `flow_rows` lines that Task 3 left further down the function.

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v
```

Expected: all pass, including the pre-existing `test_sample_type_sheets_follow_the_readme` and `test_blocks_keep_sheet_order_not_alphabetical_order`.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): sheets and README follow generation order, not the alphabet"
```

---

### Task 5: Dropdowns extend past the filled rows

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Modify: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `DROPDOWN_SPARE_ROWS = 500`.

- [ ] **Step 1: Write the failing tests**

Add `DROPDOWN_SPARE_ROWS` to the import block at the top of `nextseek_api/tests/test_sample_workbook.py`, then append:

```python
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_dropdowns_reach_past_the_filled_rows(_ctx, _fields, tmp_path):
    """A researcher adding samples must keep the dropdown, not lose it at the
    first empty row."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_cv_df(), str(out))
    for rule in load_workbook(out)["MUS"].data_validations.dataValidation:
        last = int(str(rule.sqref).split(":")[1].lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
        assert last == 2 + DROPDOWN_SPARE_ROWS  # one data row, plus the spare


def test_a_dropdown_range_never_starts_below_its_end():
    """A frame with no data rows must not produce A2:A1, which Excel rejects.
    Called directly: write_samples_workbook always writes at least one row, so
    the guard is unreachable through the public API."""
    from openpyxl import Workbook

    from nextseek_api.services.sample_workbook import _apply_dropdowns

    ws = Workbook().active
    _apply_dropdowns(ws, ["DataType"], {"DataType": "filetype"},
                     {"filetype": "'Controlled Vocabularies'!$A$2:$A$9"}, 0)
    rule, = ws.data_validations.dataValidation
    start, end = (int(part.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
                  for part in str(rule.sqref).split(":"))
    assert start == 2
    assert end == 2 + DROPDOWN_SPARE_ROWS
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -k "reach_past or never_starts_below" -v
```

Expected: `test_dropdowns_reach_past_the_filled_rows` FAILS — `assert 2 == 502`.

- [ ] **Step 3: Write the implementation**

In `nextseek_api/services/sample_workbook.py`, add the constant beside `CV_SHEET`:

```python
# How far a dropdown reaches below the last filled row. A download is a
# starting point, not a finished sheet: a researcher adding samples must keep
# the dropdown. Full-column validation would do it too, but some tools slow
# noticeably with it applied across many columns.
DROPDOWN_SPARE_ROWS = 500
```

In `_apply_dropdowns`, replace the final line:

```python
        rule.add(f"{letter}2:{letter}{max(row_count + 1, 2) + DROPDOWN_SPARE_ROWS}")
```

and add to its docstring:

```
    The range runs DROPDOWN_SPARE_ROWS past the last filled row, so the
    dropdown survives a researcher adding samples underneath.
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v
```

Expected: all pass, `test_dropdowns_warn_rather_than_reject` included.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): dropdowns reach 500 rows past the filled data"
```

---

### Task 6: House vocabularies from production

**Files:**
- Rewrite: `nextseek_api/services/controlled_vocabularies.json`
- Modify: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `_load_vocabularies` already reads `field_map` and `vocabularies` and ignores every other key, so the new `variants` block needs no code change.
- Produces: no new Python names.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
def _vocabularies():
    from nextseek_api.services.sample_workbook import _load_vocabularies
    return _load_vocabularies()


def test_every_governed_column_resolves_to_a_real_vocabulary():
    field_map, vocabularies = _vocabularies()
    assert field_map, "the file must load; a parse error costs every dropdown"
    assert all(name in vocabularies for name in field_map.values())


def test_the_house_layout_terms_are_the_ones_production_uses():
    """Production holds Paired End 3511 times and bare 'paired' 52 times. The
    dropdown used to offer only 'paired'."""
    _, vocabularies = _vocabularies()
    assert vocabularies["library_layout"] == ["Paired End", "Single End"]


def test_imaging_formats_are_offered():
    """GEO's filetype list has no term for any of these; production has
    thousands of rows of them."""
    _, vocabularies = _vocabularies()
    for term in ("TIF", "OIB", "CZI", "DICOM", "LIF", "ND2"):
        assert term in vocabularies["filetype"]


def test_illumina_instruments_are_always_prefixed():
    """GEO is inconsistent -- 'Illumina MiSeq' but bare 'NextSeq 500' -- and
    production split on exactly that seam. Offering both forms is what caused
    the split, so the bare ones are gone."""
    _, vocabularies = _vocabularies()
    models = vocabularies["instrument_model"]
    for bare in ("NextSeq 500", "NextSeq 550", "NextSeq 1000", "NextSeq 2000"):
        assert bare not in models
        assert f"Illumina {bare}" in models


def test_nextseek_only_instruments_are_present():
    _, vocabularies = _vocabularies()
    assert "Singular G4" in vocabularies["instrument_model"]
    assert "PromethION P2 Solo" in vocabularies["instrument_model"]


def test_no_vocabulary_offers_the_same_term_twice():
    """A duplicate renders as two identical dropdown entries."""
    _, vocabularies = _vocabularies()
    for name, terms in vocabularies.items():
        assert len(terms) == len(set(terms)), name


def test_no_term_would_be_mangled_on_its_way_into_a_cell():
    from nextseek_api.services.sample_workbook import _safe_cell_value
    _, vocabularies = _vocabularies()
    for terms in vocabularies.values():
        for term in terms:
            assert _safe_cell_value(term) == term


def test_the_variants_block_documents_only_real_vocabularies():
    """It is documentation, but documentation that drifts is worse than none."""
    import json
    from nextseek_api.services.sample_workbook import CV_PATH
    doc = json.loads(CV_PATH.read_text())
    documented = set(doc["variants"]) - {"_excluded"}
    assert documented <= set(doc["vocabularies"])
    assert set(doc["variants"]["_excluded"]) <= set(doc["vocabularies"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -k "house_layout or imaging_formats or illumina or nextseek_only or variants_block" -v
```

Expected: five failures. `test_the_house_layout_terms_are_the_ones_production_uses` fails with `['single', 'paired'] != ['Paired End', 'Single End']`; `test_the_variants_block_documents_only_real_vocabularies` fails with `KeyError: 'variants'`.

- [ ] **Step 3: Regenerate the file**

Write this generator to `/tmp/gen_cv.py` and run it from the worktree root. It derives `instrument_model` from the existing file so GEO's 82 entries are not retyped, applies the four Illumina renames, and appends the two NExtSEEK-only instruments. It is a one-shot: **do not commit the script**, only its output.

```python
import json, collections
P = 'nextseek_api/services/controlled_vocabularies.json'
old = json.load(open(P))
RENAME = {"NextSeq 500": "Illumina NextSeq 500", "NextSeq 550": "Illumina NextSeq 550",
          "NextSeq 1000": "Illumina NextSeq 1000", "NextSeq 2000": "Illumina NextSeq 2000"}
instruments = [RENAME.get(t, t) for t in old['vocabularies']['instrument_model']]
instruments += ["Singular G4", "PromethION P2 Solo"]

doc = collections.OrderedDict()
doc["_source"] = (
    "NExtSEEK's own house vocabularies, chosen from the values NExtSEEK actually "
    "holds (production, 166,235 samples, measured 2026-08-21). One term per "
    "concept, so future curation converges on a single spelling. Where GEO or SRA "
    "already had the concept and NExtSEEK agrees, the repository's spelling is "
    "kept. Illumina instruments are always prefixed 'Illumina ' -- GEO is "
    "inconsistent there, and production split on exactly that seam."
)
doc["_field_map"] = "Which sample column each vocabulary governs."
doc["_variants"] = (
    "Documentation only; nothing reads this. Records which observed spellings "
    "each canonical term absorbs, with the production row count the judgement "
    "rested on, so a later normalisation pass is mechanical rather than re-derived."
)
doc["field_map"] = old["field_map"]
doc["vocabularies"] = {
    "library_layout": ["Paired End", "Single End"],
    "library_source": ["Genomic", "Transcriptomic"],
    "library_strategy": ["RNA-Seq", "Bulk RNA-Seq", "scRNA-Seq", "WGS", "Amplicon",
                         "Targeted Capture", "Hi-C", "Other"],
    "library_selection": ["Other", "cDNA", "PCR", "RT-PCR", "PolyA",
                          "Hybrid Selection", "Inverse rRNA", "RANDOM"],
    "filetype": ["FASTQ", "BAM", "FAST5", "POD5", "H5AD", "H5", "MTX", "MCOOL",
                 "HIC", "RDS", "MZML", "TIF", "OIB", "OIF", "LIF", "CZI", "ND2",
                 "DICOM", "NII", "JPG", "PNG", "AVI", "MOV", "CSV", "TSV", "TXT",
                 "XLSX", "XML", "PDF", "DOCX", "PPTX", "PZFX", "SLX", "SBD"],
    "instrument_model": instruments,
}
doc["variants"] = {
  "library_layout": {
      "Paired End": ["Paired End (3511)", "Paired (789)", "PAIRED (705)",
                     "Paired-end (218)", "Paired end (118)", "paired (52)"],
      "Single End": ["not present in production"]},
  "library_source": {
      "Genomic": ["Genomic (1504)", "GENOMIC (1284)"],
      "Transcriptomic": ["transcriptomic (1034)", "TRANSCRIPTOMIC (907)",
                         "Transcriptomic (688)"]},
  "library_strategy": {
      "RNA-Seq": ["RNA-Seq (2001)", "RNA-seq (196)", "RNAseq (102)", "RNA-SEQ (2)"],
      "Bulk RNA-Seq": ["Bulk RNA-Seq (186)"], "scRNA-Seq": ["scRNA-Seq (142)"],
      "WGS": ["WGS (1065)"], "Amplicon": ["Amplicon (755)", "AMPLICON (563)"],
      "Targeted Capture": ["Targeted Capture (69)"], "Hi-C": ["Hi-C (12)"],
      "Other": ["OTHER (32)"]},
  "library_selection": {
      "Other": ["other (1489)", "Other (25)"], "cDNA": ["cDNA (1466)"],
      "PCR": ["PCR (1318)"], "RT-PCR": ["RT-PCR (426)"], "PolyA": ["PolyA (301)"],
      "Hybrid Selection": ["Hybrid Selection (44)"],
      "Inverse rRNA": ["Inverse rRNA selection (27)"],
      "RANDOM": ["RANDOM (22)", "Random (4)"]},
  "filetype": {
      "TIF": ["tif (5877)", "TIF (2447)", "tiff (666)", ".tif (24)", "TIFF (4)",
              "ome.tif (3)"],
      "FASTQ": ["FASTQ (4202)", "fastq (1655)", "Fastq (332)", "fastq.gz (226)",
                "FastQ (202)"],
      "OIB": ["oib (4799)"], "JPG": ["jpg (1172)", "JPG (17)", "JPEG (7)", "jpeg (4)"],
      "LIF": [".lif (1038)"], "TXT": ["TXT (690)"], "XML": ["tiff_metadata.xml (636)"],
      "PNG": ["png (463)", "PNG (3)"], "CZI": [".czi (360)", "CZI (276)", "czi (109)"],
      "DICOM": [".dcm (345)", "DICOM (239)", "Dicom (97)"],
      "BAM": ["bam (95)", "BAM (34)"], "H5AD": ["H5AD (47)"],
      "ND2": ["nd2 (45)", "ND2 (9)"], "OIF": ["oif (45)"], "NII": ["nii.gz (40)"],
      "H5": ["h5 (25)", "H5 (5)", "HDF5 (2)", "h5Ad (2)"],
      "XLSX": ["XLSX (15)", "xlsx (1)"], "MTX": ["MTX (10)"], "FAST5": ["fast5 (8)"],
      "HIC": ["hic (8)"], "CSV": ["CSV (7)"], "PZFX": ["PZFX (7)", "pzfx (1)"],
      "MCOOL": ["mcool (4)"], "AVI": ["avi (4)"], "MZML": ["mzML (4)"],
      "POD5": ["pod5 (3)"], "RDS": ["rds (3)", "RDS (2)"], "SLX": ["slx (2)"],
      "SBD": ["sbd (2)"], "PDF": ["pdf (1)"], "DOCX": ["docx (1)"],
      "PPTX": ["pptx (1)"], "MOV": ["mov (1)"], "TSV": ["TSV (1)"]},
  "instrument_model": {
      "Illumina NovaSeq 6000": ["Illumina NovaSeq 6000 (1864)", "NovaSeq 6000 (397)",
                                "Illumina NovaSeq6000 (99)",
                                "NovaSeq 6000, Illumina (24)", "NovaSeq6000 (21)"],
      "Illumina MiSeq": ["Illumina MiSeq (1317)"],
      "Illumina NextSeq 500": ["Illumina NextSeq 500 (526)", "NextSeq 500 (390)",
                               "Illumina NextSeq500 (232)"],
      "Illumina NextSeq 550": ["Illumina NextSeq 550 (92)"],
      "Illumina NextSeq 2000": ["NextSeq 2000 (110)", "Illumina NextSeq 2000 (32)"],
      "Element AVITI": ["Element Aviti (190)", "Aviti (76)", "Element AVITI (4)"],
      "Illumina HiSeq 4000": ["Illumina HiSeq 4000 (179)"],
      "Illumina HiSeq 2500": ["Illumina HiSeq 2500 (13)"],
      "Singular G4": ["Singular G4 (130)", "SingularG4 (8)"],
      "Illumina NovaSeq X": ["Illumina NovaSeqX (80)", "Illumina NovaSeq X (32)",
                             "Illumina NovaSeq X 25B (27)"],
      "PromethION": ["Prometheon 24 (19)"],
      "PromethION P2 Solo": ["P2 Solo (9)"]},
  "_excluded": {
      "library_layout": ["3DGE (384) -- a prep method, not a layout",
                         "Duplex Sequencing Mouse Mutagenesis Panel v2.0 (32) -- a kit",
                         "Paired-End 2x75nt (4) -- a read configuration"],
      "library_selection": ["Whole Genome Sequencing (292) -- a strategy, not a selection"],
      "filetype": ["22 distinct 'Ki-67 proliferation index = ...' values (1 row each)",
                   "Targeted LC-MS metabolomics + stable-isotope tracing (126)",
                   "Super-resolution localization microscopy (STORM) (76)",
                   "MALDI-MSI spatial molecular images / annotated features (42)",
                   "IHC / histology (Ki-67, H&E) (35)", "PRISM (18)",
                   "mzML (converted; original .raw not deposited to PXD055726) (4)",
                   "Mutational spectra catalogue (absolute) / (differential) (1 each)"],
      "instrument_model": [
          "61 'NovaSeq 60NN' counter values, 1 row each, across three spellings",
          '{"vendor": "10x, Illumina", ...} (47) -- a JSON blob',
          "P.BMC-240301-V1_Illumina_NextSeq_Standard_Workflow.doc (2) -- a filename",
          "NextSeq500:George (7) -- an instrument nickname",
          "Illumina HiSeq (95) -- no model; ambiguous between six GEO HiSeq entries"]},
}
open(P, 'w').write(json.dumps(doc, indent=2) + "\n")
print("filetype", len(doc["vocabularies"]["filetype"]),
      "instrument_model", len(doc["vocabularies"]["instrument_model"]))
```

Run it:

```bash
python3 /tmp/gen_cv.py && rm /tmp/gen_cv.py
```

Expected output: `filetype 34 instrument_model 84`

- [ ] **Step 4: Run tests to verify they pass**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v
```

Expected: all pass. `test_dropdowns_warn_rather_than_reject` must still pass — the fixture value `"paired"` is now outside the vocabulary, which is exactly the case `warning` exists for.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/controlled_vocabularies.json nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): NExtSEEK's own house vocabularies, derived from production"
```

---

### Task 7: Whole-feature verification

**Files:** none modified — this task only runs things and records what it found.

- [ ] **Step 1: Run both test files in full**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py nextseek_api/tests/test_sample_provenance.py -v
```

Expected: all pass, no errors, no warnings about unclosed workbooks.

- [ ] **Step 2: Confirm no pre-existing failures were introduced**

```bash
./scripts/run_tests.sh nextseek_api/tests
```

The wider suite has a known pre-existing baseline of 10 failures / 410 errors rooted in `seek/timeline/services/nhp_cache_test.py`, unrelated to this branch. Compare against the baseline:

```bash
git stash && ./scripts/run_tests.sh nextseek_api/tests 2>&1 | tail -3 && git stash pop
```

Expected: the failure and error counts match before and after.

- [ ] **Step 3: Generate a real workbook and look at it**

```bash
docker exec nextseek sh -c 'cd /app && uv run python -c "
import django, os
os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\", \"dmac.settings\")
django.setup()
import pandas as pd
from nextseek_api.services.sample_workbook import write_samples_workbook
from seek.models import Samples
rows = list(Samples.objects.values(\"uuid\")[:400])
df = pd.DataFrame(rows)
df[\"Parent\"] = \"\"
write_samples_workbook(df, \"/tmp/check.xlsx\")
print(\"written\")
"'
docker cp nextseek:/tmp/check.xlsx /tmp/check.xlsx
```

Then confirm the structure:

```bash
python3 -c "
from openpyxl import load_workbook
wb = load_workbook('/tmp/check.xlsx')
print('sheets:', wb.sheetnames[:4])
if 'How this data flowed' in wb.sheetnames:
    for row in wb['How this data flowed'].iter_rows(max_row=6):
        print('  ', [c.value for c in row if c.value])
"
```

Expected: `README` first, `How this data flowed` second (if the graph is reachable), then sample tabs in generation order. Chains read left to right.

- [ ] **Step 4: Report**

State plainly, with the actual numbers: how many tests pass, whether the wider-suite counts matched the baseline, and what the generated workbook's first four sheets were. If the flow sheet is absent, say so and say why (an unreachable graph and no `Parent` column both produce that, and they are different problems).

---

## Notes for the implementer

- **`build_provenance_lines` is deleted, not deprecated.** It is exported and tested today; Task 3 removes it and migrates its tests. Nothing outside `sample_workbook.py` and its test file imports it — confirm with `grep -rn "build_provenance_lines" --include='*.py' .` before deleting, and if that grep finds a third caller, stop and report it rather than working around it.
- **The `_no_graph` autouse fixture** in `test_sample_workbook.py` patches `load_derivation_hops` to return `[]`. That is why the new tests also patch `load_assay_titles` — without it, the `Parent`-column fallback path reaches the ORM.
- **Do not change `errorStyle`.** See Global Constraints.
- **Issue #110 stays open.** Task 4 moves the Neo4j call earlier in the function but does not change its 5s bound, and that bound is still unprofiled at production scale.

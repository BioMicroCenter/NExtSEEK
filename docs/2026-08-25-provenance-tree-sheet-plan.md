# Provenance tree sheet — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "How this data flowed" sheet's flat chains with an indented tree, so every sample type is shown under what it was derived from.

**Architecture:** One new pure function, `build_provenance_tree`, replaces `build_provenance_rows` in `nextseek_api/services/sample_provenance.py`. It walks the same `edges` data depth-first from each root and returns one pre-indented string per line. `_write_flow_sheet` in `nextseek_api/services/sample_workbook.py` writes those strings into a single monospace column instead of alternating type/arrow cells.

**Tech Stack:** Python 3.14, Django, openpyxl 3.1.5, pytest. No new dependencies.

**Design:** [`2026-08-25-provenance-tree-sheet-design.md`](2026-08-25-provenance-tree-sheet-design.md)

## Global Constraints

- Do **not** modify `derivation_edges` or `sample_type_depths`. They are correct and 14 tests cover them.
- No new dependencies — no graphviz, no matplotlib, no headless browser.
- The sheet keeps its name (`FLOW_SHEET = "How this data flowed"`) and its position (index 1, after README).
- Assays are shown per **hop**, never merged per type.
- Two distinct guards, both required: `(cycle)` tests the *current path*; `(expanded above)` tests *everything expanded so far*.
- Run tests through the container: `./scripts/run_tests.sh <paths>`. The host route does not work on macOS — `mysqlclient` will not build there.

---

### Task 1: The tree builder

Adds `build_provenance_tree` alongside the existing `build_provenance_rows`. Nothing is wired up yet, so the workbook is unchanged and every existing test still passes.

**Files:**
- Modify: `nextseek_api/services/sample_provenance.py` (add after `build_provenance_rows`, which ends at line 171)
- Test: `nextseek_api/tests/test_sample_provenance.py` (append)

**Interfaces:**
- Consumes: `derivation_edges(...) -> dict[tuple[str, str], set[str]]` and `sample_type_depths(...) -> dict[str, int]`, both already in the module.
- Produces: `build_provenance_tree(edges: Mapping[tuple[str, str], set[str]]) -> list[str]` — one display line per node, already indented. Task 2 consumes this.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_provenance.py`:

```python
import re

from nextseek_api.services.sample_provenance import build_provenance_tree


def _tree(edges):
    return build_provenance_tree(edges)


def _type_of(line):
    """The sample type on a tree line, stripped of indent, connector and assays."""
    text = re.sub(r"^[│ ]*(?:├── |└── )?", "", line)
    return text.split("   ")[0].strip()


def test_a_root_is_unindented_and_its_child_is_indented():
    lines = _tree({("PAT", "PAV"): set()})
    assert lines == ["PAT", "└── PAV"]


def test_assays_render_in_brackets_after_the_type():
    lines = _tree({("PAT", "PAV"): {"Consent"}})
    assert lines[1] == "└── PAV   [Consent]"


def test_a_hop_without_an_assay_gets_no_bracket():
    assert _tree({("DNA", "D.SEQ"): set()})[1] == "└── D.SEQ"


def test_several_assays_on_one_hop_join_sorted_in_brackets():
    # NOT `test_several_assays_on_one_hop_join_sorted` -- that name is already
    # taken at line 157 by a build_provenance_rows test that Task 2 deletes.
    # Reusing it here would silently shadow that test until then.
    lines = _tree({("TIS", "D.IMG"): {"Imaging", "Histology"}})
    assert lines[1] == "└── D.IMG   [Histology, Imaging]"


def test_a_non_final_child_uses_the_tee_connector():
    edges = {("TIS", "DNA"): set(), ("TIS", "RNA"): set()}
    assert _tree(edges) == ["TIS", "├── DNA", "└── RNA"]


def test_a_grandchild_of_a_tee_keeps_the_trunk():
    """The │ must continue past a child that has siblings below it."""
    edges = {("TIS", "DNA"): set(), ("DNA", "D.SEQ"): set(), ("TIS", "RNA"): set()}
    assert _tree(edges) == ["TIS", "├── DNA", "│   └── D.SEQ", "└── RNA"]


def test_a_type_with_two_parents_is_expanded_once():
    """The DAG is not a tree: expanding every occurrence explodes the sheet."""
    edges = {("A", "X"): set(), ("B", "X"): set(), ("X", "Y"): set()}
    lines = _tree(edges)
    assert sum(1 for line in lines if _type_of(line) == "Y") == 1
    assert [line for line in lines if line.endswith("(expanded above)")]


def test_a_childless_repeat_is_shown_in_full_not_deferred():
    """(expanded above) would be noise on a leaf -- there is nothing to expand."""
    edges = {("A", "X"): set(), ("B", "X"): set()}
    assert not [line for line in _tree(edges) if line.endswith("(expanded above)")]


def test_a_cycle_terminates_and_is_marked():
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    lines = _tree(edges)
    assert [line for line in lines if line.endswith("(cycle)")]


def test_every_type_in_every_hop_appears_somewhere():
    """Nothing may be silently dropped -- that would be a correctness bug."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set(),
             ("TIS", "D.IMG"): set(), ("DNA", "D.SEQ"): set()}
    shown = {_type_of(line) for line in _tree(edges)}
    for parent, child in edges:
        assert parent in shown and child in shown


def test_roots_appear_unindented_in_sorted_order():
    edges = {("PAT", "PAV"): set(), ("MUS", "TIS"): set()}
    lines = _tree(edges)
    assert [line for line in lines if line and not line.startswith((" ", "│", "├", "└"))] == [
        "MUS", "PAT",
    ]


def test_root_trees_are_separated_by_a_blank_line():
    edges = {("PAT", "PAV"): set(), ("MUS", "TIS"): set()}
    assert _tree(edges) == ["MUS", "└── TIS", "", "PAT", "└── PAV"]


def test_no_edges_yields_no_lines():
    assert build_provenance_tree({}) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py -v`

Expected: FAIL — `ImportError: cannot import name 'build_provenance_tree' from 'nextseek_api.services.sample_provenance'`

- [ ] **Step 3: Write the implementation**

Append to `nextseek_api/services/sample_provenance.py`, after `build_provenance_rows`:

```python
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
    return lines
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py -v`

Expected: PASS — 35 passed (22 existing + 13 new). The existing `build_provenance_rows` tests still pass because that function is untouched.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_provenance.py nextseek_api/tests/test_sample_provenance.py
git commit -m "feat(download): a tree builder for the provenance sheet"
```

---

### Task 2: Write the tree to the sheet, and retire the flat chains

Rewires `_write_flow_sheet` to the tree, then deletes `build_provenance_rows` and the eight tests that only covered it. The old function must not outlive its last caller.

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py` — imports (line 33-38), constants (line 57-58), `_write_flow_sheet` (line 367-384), caller (line 451, 477-478)
- Modify: `nextseek_api/services/sample_provenance.py` — delete `build_provenance_rows` and `_arrow`
- Test: `nextseek_api/tests/test_sample_workbook.py`, `nextseek_api/tests/test_sample_provenance.py`

**Interfaces:**
- Consumes: `build_provenance_tree(edges) -> list[str]` from Task 1.
- Produces: `_write_flow_sheet(book, lines: list[str]) -> None` — writes column A only, monospace.

- [ ] **Step 1: Write the failing test**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
def test_the_flow_sheet_writes_one_monospace_column():
    from openpyxl import Workbook
    from nextseek_api.services.sample_workbook import _write_flow_sheet, FLOW_SHEET

    book = Workbook()
    _write_flow_sheet(book, ["PAT", "└── PAV   [Consent]"])
    ws = book[FLOW_SHEET]
    assert ws["A1"].value == "PAT"
    assert ws["A2"].value == "└── PAV   [Consent]"
    assert ws["B1"].value is None
    assert ws["A1"].font.name == "Consolas"


def test_the_flow_sheet_is_skipped_when_there_is_no_lineage():
    from openpyxl import Workbook
    from nextseek_api.services.sample_workbook import _write_flow_sheet, FLOW_SHEET

    book = Workbook()
    _write_flow_sheet(book, [])
    assert FLOW_SHEET not in book.sheetnames
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -k flow_sheet -v`

Expected: FAIL — `AssertionError` on `ws["B1"].value is None`, because the current writer puts alternating cells across the row.

- [ ] **Step 3: Replace the constants and the writer**

In `nextseek_api/services/sample_workbook.py`, replace lines 57-58:

```python
FLOW_TYPE_WIDTH = 14
FLOW_ARROW_WIDTH = 34
```

with:

```python
# The tree's box-drawing characters only line up in a fixed-width font.
FLOW_FONT = "Consolas"
FLOW_MAX_WIDTH = 160
```

Replace `_write_flow_sheet` (lines 367-384) with:

```python
def _write_flow_sheet(book, lines: list[str]) -> None:
    """One indented tree line per row, in a single fixed-width column.

    Its own sheet rather than a README section: README's columns are sized
    46 / 34 / 100 for the summary and column tables, which would chop the tree.

    The font is set per cell rather than on the column because openpyxl column
    styles do not apply to cells that already carry a value.
    """
    if not lines:
        return
    ws = book.create_sheet(FLOW_SHEET, 1)
    for index, line in enumerate(lines, start=1):
        _write_cell(ws, index, 1, line).font = Font(name=FLOW_FONT)
    ws.column_dimensions["A"].width = min(
        max(len(line) for line in lines) + 2, FLOW_MAX_WIDTH)
```

- [ ] **Step 4: Rewire the import and the caller**

In the import block at lines 33-38, replace `build_provenance_rows` with `build_provenance_tree`:

```python
from nextseek_api.services.sample_provenance import (
    SAMPLE_TYPE_RE,
    build_provenance_tree,
    derivation_edges,
    sample_type_depths,
)
```

At line 451, replace:

```python
    flow_rows = build_provenance_rows(edges, depths)
```

with:

```python
    flow_lines = build_provenance_tree(edges)
```

At lines 477-478, replace:

```python
        _write_readme(book, blocks, has_flow_sheet=bool(flow_rows))
        _write_flow_sheet(book, flow_rows)
```

with:

```python
        _write_readme(book, blocks, has_flow_sheet=bool(flow_lines))
        _write_flow_sheet(book, flow_lines)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py -v`

Expected: PASS for the two new tests. Any existing test asserting alternating type/arrow cells now fails — those are removed in Step 6.

- [ ] **Step 6: Delete the retired function and its tests**

In `nextseek_api/services/sample_provenance.py`, delete `_arrow` (lines 103-105) and `build_provenance_rows` (lines 108-171) entirely.

In `nextseek_api/tests/test_sample_provenance.py`, delete the `_rows` helper and these eight tests, all of which only exercised the deleted function:

- `test_a_chain_reads_left_to_right_across_the_row`
- `test_a_hop_without_an_assay_gets_a_plain_arrow`
- `test_several_assays_on_one_hop_join_sorted`
- `test_every_hop_appears_exactly_once`
- `test_chains_sort_by_the_depth_of_their_first_type`
- `test_a_chain_may_start_mid_pipeline`
- `test_a_cycle_terminates_and_visits_each_type_once_per_chain`
- `test_no_edges_yields_no_rows`

Each has a Task 1 counterpart: bracket rendering, sorted assays, cycle termination, hop coverage, root ordering and the empty case are all covered by the new tests.

In `nextseek_api/tests/test_sample_workbook.py`:

- Remove `FLOW_ARROW_WIDTH` (line 15) and `FLOW_TYPE_WIDTH` (line 18) from the import block.
- Delete `_flow_rows` (line 562) — nothing else uses it once the two tests below go.
- Delete `test_a_chain_occupies_one_row_across_columns` (line 579). Its assertion `["PAT", "------>", "PAV", "------>", "TIS"] in rows` describes the layout being removed. Replace it with:

```python
@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_the_tree_puts_a_root_above_its_indented_child(_ctx, _fields, _assays, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    ws = load_workbook(out)[FLOW_SHEET]
    column_a = [ws.cell(row=r, column=1).value for r in range(1, ws.max_row + 1)]
    assert "PAT" in column_a
    assert any(str(v).startswith(("├── ", "└── ")) for v in column_a if v)
```

- Delete `test_the_flow_sheet_uses_its_own_alternating_widths` (line 619). It asserts `FLOW_TYPE_WIDTH` on columns A/C and `FLOW_ARROW_WIDTH` on B/D, all four of which cease to exist. Replace it with:

```python
@patch(f"{_MOD}.load_assay_titles", return_value={})
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_the_flow_sheet_sizes_its_one_column_to_the_widest_line(_ctx, _fields, _assays, tmp_path):
    """The justification for a separate sheet is that it can size its own
    column. Nothing else asserts the width is applied, so it could be dropped
    silently and the tree would render clipped."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_flow_df(), str(out))
    ws = load_workbook(out)[FLOW_SHEET]
    widest = max(len(str(ws.cell(row=r, column=1).value or ""))
                 for r in range(1, ws.max_row + 1))
    assert ws.column_dimensions["A"].width == min(widest + 2, FLOW_MAX_WIDTH)
```

Add `FLOW_MAX_WIDTH` to the import block in place of the two deleted names.

Three tests in this range need **no** change and must keep passing:
`test_the_flow_sheet_sits_directly_after_the_readme` (line 570),
`test_no_flow_sheet_when_there_is_no_lineage` (line 590),
`test_the_readme_points_at_the_flow_sheet` (line 607).

- [ ] **Step 7: Confirm nothing still references the deleted names**

Run:

```bash
grep -rn 'build_provenance_rows\|FLOW_TYPE_WIDTH\|FLOW_ARROW_WIDTH\|_arrow' --include='*.py' nextseek_api seek
```

Expected: no output.

- [ ] **Step 8: Run the full affected suite**

Run: `./scripts/run_tests.sh nextseek_api/tests/test_sample_provenance.py nextseek_api/tests/test_sample_workbook.py -v`

Expected: PASS, no failures, no errors.

- [ ] **Step 9: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/services/sample_provenance.py nextseek_api/tests/test_sample_workbook.py nextseek_api/tests/test_sample_provenance.py
git commit -m "feat(download): the flow sheet becomes an indented tree"
```

---

### Task 3: Verify against the real instance

Unit tests prove the shape; only a real workbook proves the numbers. `nextseek_api/` is baked into the image, so the rebuild is not optional.

**Files:**
- Create: none (verification only)

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.

- [ ] **Step 1: Rebuild so the container carries the new code**

Run: `./startup.sh rebuild`

Expected: `✓ app rebuilt and restarted`. The GHCR baseline warning is pre-existing and unrelated.

- [ ] **Step 2: Confirm the container actually has the new function**

Run:

```bash
docker exec nextseek grep -c build_provenance_tree /app/nextseek_api/services/sample_workbook.py
```

Expected: `2`. If it returns `0`, the rebuild did not take — do not continue, because `./scripts/run_tests.sh` bind-mounts the checkout and passes regardless of the image.

- [ ] **Step 3: Generate a real workbook and count the lines**

Write `/tmp/verify_tree.py`, copy it in with `docker cp`, and run it with `uv run python manage.py shell < /tmp/verify_tree.py`:

```python
import pandas as pd
from django.db import connections
from nextseek_api.services import sample_workbook as sw
from nextseek_api.services.sample_provenance import (
    derivation_edges, sample_type_depths, build_provenance_tree)

with connections["seek"].cursor() as cur:
    cur.execute("""SELECT s.uuid, JSON_UNQUOTE(JSON_EXTRACT(s.json_metadata,'$.Parent'))
                     FROM samples s JOIN projects_samples ps ON ps.sample_id=s.id
                    WHERE ps.project_id=1""")
    rows = cur.fetchall()
df = pd.DataFrame(rows, columns=["uuid", "Parent"])
df["sample_type"] = df["uuid"].astype(str).str.extract(sw.SAMPLE_TYPE_RE, expand=False)
edges = derivation_edges(df, sw.load_assay_titles(df["uuid"].astype(str).tolist()), None)
lines = build_provenance_tree(edges)
print(f"hops={len(edges)} lines={len(lines)}")
print(f"cycle markers={sum(1 for x in lines if x.endswith('(cycle)'))}")
print(f"deferred={sum(1 for x in lines if x.endswith('(expanded above)'))}")
print("\n".join(lines[:20]))
```

Expected: `hops=150`, `lines` close to 177 (the design measured 178 including a trailing blank), a non-zero count for both markers, and the first 20 lines showing `A` then an indented `D.IMG` subtree.

- [ ] **Step 4: Confirm the workbook opens with the sheet intact**

Append to the same script and re-run it. The `LIMIT 6000` is deliberate: the full 51,359 rows OOM-kill the container inside `json_normalize`, a pre-existing limitation unrelated to this change.

```python
import openpyxl
from seek.views import parse_children_uids

with connections["seek"].cursor() as cur:
    cur.execute("""SELECT s.uuid, s.json_metadata
                     FROM samples s JOIN projects_samples ps ON ps.sample_id=s.id
                    WHERE ps.project_id=1 ORDER BY s.id LIMIT 6000""")
    full = cur.fetchall()

out = "/tmp/verify_tree.xlsx"
sw.write_samples_workbook(
    parse_children_uids(pd.DataFrame(full, columns=["uuid", "json_metadata"])), out)

ws = openpyxl.load_workbook(out)["How this data flowed"]
assert ws["A1"].font.name == "Consolas", ws["A1"].font.name
assert ws["B1"].value is None, "column B must be empty -- the tree is one column"
print(f"sheet OK: {ws.max_row} rows, max_column={ws.max_column}")
print("\n".join(str(ws.cell(row=r, column=1).value) for r in range(1, 16)))
```

Expected: `max_column=1`, and the printed lines showing a root followed by `├──`/`└──` children.

- [ ] **Step 5: Commit any fixes, then report**

If Steps 3-4 revealed nothing, there is nothing to commit. Report the measured `hops`, `lines`, and both marker counts.

---

## Out of scope, and why

- **The full-download OOM.** Generating a workbook for all 51,359 samples OOM-kills the container (`exit=137`) inside `parse_children_uids`'s `json_normalize`, before any of this code runs. It predates this change and needs its own investigation.
- **Deploying to `nextseek-dev.mit.edu`.** That host has its own clone and image; see `DEPLOYMENT.md` §3.1.

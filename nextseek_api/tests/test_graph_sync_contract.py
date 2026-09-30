"""The graph contract (``nextseek_graph/schema.py``) against the document of record and every copy of its names.

One blocking test module (``ci/blocking_lanes.py`` picks it up by its name). It imports the contract and, for the
writer's copies, ``nextseek_api.graph_sync``; everything else it reads as files, so the blocking step never loads the
Nessie agent stack. Every failure names the doc section, row or file it read.

- The module: standard library only, no Cypher text, frozen types, ``__all__`` exact, one group per version.
- Versions and the internal consistency of the groups.
- The four functions.
- ``docs/neo4j-schema.md``: every ``## vX.Y`` section from v1.1 on equals the contract's groups of that version.
- The copies: the writer's names are the contract's; the readers' literals equal the groups they are built from.
- The committed fallback capture names nothing the contract lacks.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from nextseek_api.graph_sync import cypher, labels, projection, verify, writer
from nextseek_graph import schema

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "neo4j-schema.md"
SCHEMA_PATH = REPO_ROOT / "nextseek_graph" / "schema.py"
INIT_PATH = REPO_ROOT / "nextseek_graph" / "__init__.py"
NESSIE = REPO_ROOT / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek"
CAPTURE_PATH = NESSIE / "context" / "neo4j_schema.json"

_CYPHER_WORD = re.compile(r"\b(MATCH|MERGE|CREATE|DELETE|SET|RETURN|UNWIND)\b")
_SUFFIX = re.compile(r"_V(\d)(\d+)$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TICKS = re.compile(r"`([^`]+)`")


# --- helpers over the contract -------------------------------------------------------------------------------------

def _tag(version: str) -> str:
    return "V" + version.replace(".", "")


def _group(prefix: str, version: str, empty):
    return getattr(schema, f"{prefix}_{_tag(version)}", empty)


def _through(version: str) -> list[str]:
    """Every version of the contract up to and including ``version``."""
    return [v for v in schema.VERSIONS if schema.version_tuple(v) <= schema.version_tuple(version)]


def _labels_through(version: str) -> frozenset[str]:
    return frozenset().union(*(_group("LABELS", v, frozenset()) for v in _through(version)))


def _sample_system_through(version: str) -> frozenset[str]:
    return frozenset().union(*(_group("SAMPLE_SYSTEM_PROPERTIES", v, frozenset()) for v in _through(version)))


def _node_properties_through(version: str, label: str) -> frozenset[str]:
    return frozenset().union(*(_group("NODE_PROPERTIES", v, {}).get(label, frozenset()) for v in _through(version)))


def _all(prefix: str, empty):
    return [(v, _group(prefix, v, empty)) for v in schema.VERSIONS]


# --- the module ----------------------------------------------------------------------------------------------------

def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@pytest.mark.parametrize("path", [SCHEMA_PATH, INIT_PATH], ids=["schema.py", "__init__.py"])
def test_the_package_imports_only_the_standard_library(path):
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"{path.name}:{node.lineno} imports from its own package"
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        for name in names:
            assert name in sys.stdlib_module_names, f"{path.name}:{node.lineno} imports {name}, not standard library"


def test_the_init_imports_nothing():
    body = [node for node in _tree(INIT_PATH).body
            if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]
    assert body == [], "nextseek_graph/__init__.py holds a docstring only"


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                ids.add(id(first.value))
    return ids


def test_the_contract_holds_no_cypher_text():
    tree = _tree(SCHEMA_PATH)
    docstrings = _docstring_nodes(tree)
    found = [node.value for node in ast.walk(tree)
             if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
             and _CYPHER_WORD.search(node.value)]
    assert found == [], f"nextseek_graph/schema.py holds Cypher text: {found}"


def _imported_names(tree: ast.Module) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    return names


def test_all_names_exactly_the_public_names():
    imported = _imported_names(_tree(SCHEMA_PATH))
    public = {name for name in vars(schema) if not name.startswith("_") and name not in imported}
    assert set(schema.__all__) == public
    assert len(schema.__all__) == len(set(schema.__all__))


def test_every_public_value_is_frozen():
    for name in schema.__all__:
        value = getattr(schema, name)
        if callable(value) or isinstance(value, str):
            continue
        if isinstance(value, frozenset):
            assert all(isinstance(v, str) for v in value), name
        elif isinstance(value, tuple):
            assert all(isinstance(v, (str, tuple)) for v in value), name
        elif isinstance(value, MappingProxyType):
            assert all(isinstance(k, str) and isinstance(v, frozenset) and all(isinstance(p, str) for p in v)
                       for k, v in value.items()), name
        else:
            pytest.fail(f"schema.{name} is a {type(value).__name__}: sets are frozensets, lists tuples, maps "
                        "MappingProxyType of frozensets")


def test_every_group_names_a_version_and_every_version_has_a_group():
    seen = set()
    for name in schema.__all__:
        match = _SUFFIX.search(name)
        if match:
            version = f"{match.group(1)}.{match.group(2)}"
            assert version in schema.VERSIONS, f"schema.{name} names version {version}, which VERSIONS lacks"
            seen.add(version)
    assert seen == set(schema.VERSIONS), f"versions without a group: {set(schema.VERSIONS) - seen}"


# --- versions ------------------------------------------------------------------------------------------------------

def test_versions_are_strictly_increasing():
    tuples = [schema.version_tuple(v) for v in schema.VERSIONS]
    assert None not in tuples
    assert all(a < b for a, b in zip(tuples, tuples[1:]))


def test_the_writer_version_is_the_newest_or_the_one_before():
    assert schema.SCHEMA_VERSION in schema.VERSIONS[-2:]


def test_the_reader_minimum_is_no_later_than_the_writer():
    assert schema.READER_MIN_VERSION in schema.VERSIONS
    assert schema.version_tuple(schema.READER_MIN_VERSION) <= schema.version_tuple(schema.SCHEMA_VERSION)


# --- internal consistency ------------------------------------------------------------------------------------------

def _pairwise_disjoint(groups: list[tuple[str, frozenset]]) -> None:
    for i, (va, a) in enumerate(groups):
        for vb, b in groups[i + 1:]:
            assert not (a & b), f"versions {va} and {vb} both define {sorted(a & b)}"


def test_every_label_and_relationship_is_in_exactly_one_version():
    _pairwise_disjoint(_all("LABELS", frozenset()))
    _pairwise_disjoint([(v, frozenset(g)) for v, g in _all("RELATIONSHIPS", {})])
    constants = {getattr(schema, name) for name in schema.__all__ if isinstance(getattr(schema, name), str)}
    for version, group in _all("LABELS", frozenset()):
        assert group <= constants, f"{version}: labels with no constant: {sorted(group - constants)}"
    for version, group in _all("RELATIONSHIPS", {}):
        assert set(group) <= constants, f"{version}: relationship types with no constant: {set(group) - constants}"


def test_patterns_use_their_own_version_types_and_known_labels():
    for version in schema.VERSIONS:
        patterns = _group("RELATIONSHIP_PATTERNS", version, ())
        assert len(patterns) == len(set(patterns)), f"{version}: a pattern is listed twice"
        assert {t for _, t, _ in patterns} == set(_group("RELATIONSHIPS", version, {})), version
        known = _labels_through(version)
        for start, _type, end in patterns:
            assert start in known and end in known, f"{version}: {start}-[{_type}]->{end} names an unknown label"


def test_node_property_groups_add_and_never_repeat():
    for version, group in _all("NODE_PROPERTIES", {}):
        for label in group:
            assert label in _labels_through(version), f"{version}: {label} is not a label yet"
            assert label not in (schema.SAMPLE, schema.ORPHAN_SAMPLE), f"{version}: {label} has its own groups"
    labels_seen = {label for _, group in _all("NODE_PROPERTIES", {}) for label in group}
    for label in labels_seen:
        _pairwise_disjoint([(v, g.get(label, frozenset())) for v, g in _all("NODE_PROPERTIES", {})])
    _pairwise_disjoint(_all("SAMPLE_SYSTEM_PROPERTIES", frozenset()))


def test_derived_from_properties_are_the_endpoints_and_the_seven_label_keys():
    assert schema.RELATIONSHIPS_V11[schema.DERIVED_FROM] == frozenset(
        schema.DERIVED_FROM_ENDPOINT_KEYS + schema.DERIVED_FROM_LABEL_KEYS)
    assert len(schema.DERIVED_FROM_LABEL_KEYS) == 7
    assert schema.DERIVED_FROM_ASSAY_KEYS == (schema.DERIVED_FROM_SINGULAR_ASSAY_KEYS
                                              + schema.DERIVED_FROM_PLURAL_ASSAY_KEYS)
    assert schema.DERIVED_FROM_LABEL_KEYS == schema.DERIVED_FROM_ASSAY_KEYS + schema.DERIVED_FROM_PROTOCOL_KEYS


def test_graphmeta_keys_are_its_property_groups():
    assert set(schema.GRAPHMETA_KEYS) == _node_properties_through(schema.SCHEMA_VERSION, schema.GRAPH_META)
    assert len(schema.GRAPHMETA_KEYS) == len(set(schema.GRAPHMETA_KEYS))


def test_legacy_statistics_are_not_attribute_properties():
    assert not schema.LEGACY_ATTRIBUTE_STATS & _node_properties_through(schema.VERSIONS[-1], schema.ATTRIBUTE)


def test_constraint_and_index_names_are_unique_and_name_real_properties():
    names = [schema.FULLTEXT_INDEX]
    for version in schema.VERSIONS:
        for kind in ("UNIQUE_CONSTRAINTS", "RANGE_INDEXES"):
            for name, label, prop in _group(kind, version, ()):
                names.append(name)
                assert label in _labels_through(version), f"{version} {name}: {label} is not a label yet"
                props = (_sample_system_through(version) if label == schema.SAMPLE
                         else _node_properties_through(version, label))
                assert prop in props, f"{version} {name}: {label}.{prop} is not a property of {label} yet"
    label, prop = schema.FULLTEXT_INDEX_ON
    assert label == schema.SAMPLE and prop in _sample_system_through(schema.VERSIONS[0])
    assert len(names) == len(set(names)), "a constraint or index name is used twice"
    assert not [n for n in names if n.startswith(schema.BUDGET_INDEX_PREFIX)]


# --- the four functions --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["1.1", "1.2", "1.10", " 1.2 ", "2.0"])
def test_at_least_the_reader_minimum(value):
    assert schema.at_least(value, schema.READER_MIN_VERSION)


@pytest.mark.parametrize("value", ["1.0", "0.9", "1", "1.x", "", "abc", None])
def test_below_the_reader_minimum_or_not_a_version(value):
    assert not schema.at_least(value, schema.READER_MIN_VERSION)


def test_at_least_orders_numerically():
    assert schema.at_least("1.10", "1.2") and not schema.at_least("1.2", "1.10")


@pytest.mark.parametrize("minimum", ["1", "abc", "", None])
def test_at_least_refuses_a_minimum_that_is_not_a_version(minimum):
    with pytest.raises(ValueError):
        schema.at_least("1.2", minimum)


@pytest.mark.parametrize("value, expected", [("1.2", (1, 2)), (" 1.10 ", (1, 10)), ("1", None), ("1.x", None),
                                             ("", None), ("abc", None), (None, None)])
def test_version_tuple(value, expected):
    assert schema.version_tuple(value) == expected


@pytest.mark.parametrize("title, label", [
    ("TIS", "T_TIS"), ("D.SEQ", "T_D_SEQ"), ("A.VCF", "T_A_VCF"), ("X Y-1", "T_X_Y_1"), ("é", "T__"), ("", "T_"),
])
def test_type_label(title, label):
    assert schema.type_label(title) == label


@pytest.mark.parametrize("label, expected", [("T_TIS", True), ("T_D_SEQ", True), ("T_", False), ("T_D.SEQ", False),
                                             ("TIS", False), ("", False), (None, False), (5, False)])
def test_is_type_label(label, expected):
    assert schema.is_type_label(label) is expected


# --- docs/neo4j-schema.md ------------------------------------------------------------------------------------------

def _doc() -> str:
    return DOC_PATH.read_text(encoding="utf-8")


def _sections() -> dict[str, str]:
    """Every ``## vX.Y`` section of the doc, by version, in the doc's order."""
    text = _doc()
    heads = list(re.finditer(r"^## v(\d+\.\d+)\b.*$", text, re.M))
    return {m.group(1): text[m.start():(heads[i + 1].start() if i + 1 < len(heads) else len(text))]
            for i, m in enumerate(heads)}


def _subsection(section: str, title: str) -> str | None:
    match = re.search(rf"^### {re.escape(title)}\s*$", section, re.M)
    if match is None:
        return None
    rest = section[match.end():]
    nxt = re.search(r"^### ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def _table(block: str) -> list[dict[str, str]]:
    """The first Markdown table in ``block``: one dict per body row, header cell to cell."""
    lines: list[str] = []
    for line in block.splitlines():
        if line.startswith("|"):
            lines.append(line)
        elif lines:
            break

    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    header = cells(lines[0])
    return [dict(zip(header, cells(line))) for line in lines[2:]]


def _ticks(cell: str) -> list[str]:
    return _TICKS.findall(cell)


def _doc_versions() -> list[str]:
    return [v for v in _sections() if schema.version_tuple(v) >= schema.version_tuple(schema.VERSIONS[0])]


def _where(version: str, part: str) -> str:
    return f"docs/neo4j-schema.md v{version}, {part}"


def _row_labels(row: dict[str, str]) -> list[str]:
    return [t for t in _ticks(row["Label"]) if not t.startswith(schema.TYPE_LABEL_PREFIX)]


def _every_doc_label() -> set[str]:
    out = set()
    for section in _sections().values():
        block = _subsection(section, "Nodes")
        if block:
            for row in _table(block):
                out.update(_row_labels(row))
    return out


def _v10_node_properties() -> dict[str, set[str]]:
    rows = _table(_subsection(_sections()["1.0"], "Nodes"))
    return {labels[0]: {t for t in _ticks(row["Properties"]) if _IDENT.fullmatch(t)}
            for row in rows if len(labels := _row_labels(row)) == 1}


def _node_table(version: str) -> dict[str, set[str]]:
    """label -> the property names the section's Nodes table gives it (Sample: the system ones)."""
    block = _subsection(_sections()[version], "Nodes")
    if block is None:
        return {}
    rows = _table(block)
    known = _every_doc_label()
    v10 = _v10_node_properties()
    out: dict[str, set[str]] = {}
    for row in rows:
        labels = _row_labels(row)
        where = _where(version, f"Nodes row {row['Label']}")
        assert labels, f"{where}: the Label cell names no backticked label"
        assert "Properties" in row, f"{where}: the table has no Properties column"
        cell = row["Properties"]
        if schema.SAMPLE in labels and "system:" in cell:
            cell = cell.split("system:", 1)[1].split("metadata:", 1)[0]
        props = {label: set() for label in labels}
        if "as v1.0" in cell:
            for label in labels:
                props[label] |= v10.get(label, set())
        for token in _ticks(cell):
            if "." in token and _IDENT.fullmatch(token.split(".", 1)[1]):
                owner, name = token.split(".", 1)
                assert owner in labels, f"{where}: {token} names a label this row does not define"
                props[owner].add(name)
            elif token in known or not _IDENT.fullmatch(token):
                continue
            else:
                assert len(labels) == 1, f"{where}: {token} is unqualified in a row of {len(labels)} labels"
                props[labels[0]].add(token)
        for label, names in props.items():
            out.setdefault(label, set()).update(names)
    return out


def _relationship_rows(version: str) -> list[dict[str, str]]:
    block = _subsection(_sections()[version], "Relationships")
    return _table(block) if block else []


_CHAIN = re.compile(r"\(:(\w+)\)|-\[:(\w+)(?:\s*\{([^}]*)\})?\]->")
_WHOLE_CHAIN = re.compile(r"\(:\w+\)(?:-\[:\w+(?:\s*\{[^}]*\})?\]->\(:\w+\))+")


def _patterns(cell: str, where: str) -> list[tuple[str, str, str, set[str]]]:
    """(start, type, end, properties in braces) for every pair of every backticked pattern in ``cell``.

    Every backticked token must be a whole ``(:Label)-[:TYPE {props}]->(:Label)`` chain: one written another way (a
    variable, a second label, a left arrow) fails, naming the row, instead of being read as no pattern at all.
    """
    out = []
    for pattern in _ticks(cell):
        assert _WHOLE_CHAIN.fullmatch(pattern), (
            f"{where}: `{pattern}` is not a chain of (:Label)-[:TYPE]->(:Label), the only form this test reads")
        parts = [m.groups() for m in _CHAIN.finditer(pattern)]
        for i in range(1, len(parts) - 1, 2):
            start, (_, rel, braces), end = parts[i - 1][0], parts[i], parts[i + 1][0]
            props = {p.strip() for p in (braces or "").split(",") if p.strip()}
            out.append((start, rel, end, props))
    return out


def _v10_relationship_properties() -> dict[str, set[str]]:
    out = {}
    for row in _relationship_rows("1.0"):
        notes = row.get("Notes", "")
        if "Properties" in notes:
            for _start, rel, _end, _ in _patterns(row["Pattern"], _where("1.0", f"Relationships row {row['Pattern']}")):
                out[rel] = set(_ticks(notes.split("Properties", 1)[1]))
    return out


def test_the_doc_sections_are_the_versions():
    assert _doc_versions() == list(schema.VERSIONS), "docs/neo4j-schema.md: the ## vX.Y headings from v1.1 on"


def test_the_writer_version_is_the_newest_section_with_a_versioning_subsection():
    versions = _doc_versions()
    with_subsection = [v for v in versions if _subsection(_sections()[v], "Versioning") is not None]
    assert with_subsection[-1] == schema.SCHEMA_VERSION, "docs/neo4j-schema.md: the newest ### Versioning"
    missing = [v for v in versions if v not in with_subsection]
    assert missing in ([], versions[-1:]), f"docs/neo4j-schema.md: only the newest section may lack one: {missing}"
    for version in with_subsection:
        if schema.version_tuple(version) >= (1, 2):
            text = " ".join(_subsection(_sections()[version], "Versioning").split())
            assert f'`GraphMeta.schema_version` reads `"{version}"`' in text, _where(version, "Versioning")


def test_the_reader_minimum_is_the_one_the_doc_names():
    found = [m.group(1) for v in _doc_versions()
             for m in [re.search(r"from (\d+\.\d+) up", " ".join((_subsection(_sections()[v], "Versioning")
                                                                  or "").split()))] if m]
    assert found and found[-1] == schema.READER_MIN_VERSION, "docs/neo4j-schema.md Versioning: 'from X up'"


@pytest.mark.parametrize("version", list(schema.VERSIONS))
def test_the_nodes_table_is_the_contract(version):
    table = _node_table(version)
    earlier = frozenset().union(*(_labels_through(v) for v in schema.VERSIONS
                                  if schema.version_tuple(v) < schema.version_tuple(version)))
    assert set(table) - earlier == _group("LABELS", version, frozenset()), _where(version, "Nodes, first column")
    assert table.get(schema.SAMPLE, set()) == _group("SAMPLE_SYSTEM_PROPERTIES", version, frozenset()), (
        _where(version, "Nodes, Sample"))
    assert table.get(schema.ORPHAN_SAMPLE, set()) == _group("ORPHAN_SAMPLE_PROPERTIES", version, frozenset()), (
        _where(version, "Nodes, OrphanSample"))
    others = {label: frozenset(props) for label, props in table.items()
              if label not in (schema.SAMPLE, schema.ORPHAN_SAMPLE) and props}
    assert others == dict(_group("NODE_PROPERTIES", version, {})), _where(version, "Nodes, Properties column")


@pytest.mark.parametrize("version", list(schema.VERSIONS))
def test_the_relationships_table_is_the_contract(version):
    rows = _relationship_rows(version)
    earlier = [v for v in schema.VERSIONS if schema.version_tuple(v) < schema.version_tuple(version)]
    earlier_types = {t: props for v in earlier for t, props in _group("RELATIONSHIPS", v, {}).items()}
    earlier_patterns = {p for v in earlier for p in _group("RELATIONSHIP_PATTERNS", v, ())}
    v10 = _v10_relationship_properties()
    found: dict[str, set[str]] = {}
    patterns = set()
    for row in rows:
        where = _where(version, f"Relationships row {row['Pattern']}")
        column = set(_ticks(row["Properties"])) if "Properties" in row else set()
        for start, rel, end, braces in _patterns(row["Pattern"], where):
            patterns.add((start, rel, end))
            if rel in earlier_types:
                # Each relationship type belongs to one version's group, so a later section may restate an earlier
                # type but not add to it: a new pattern or property for it would have no group to go in.
                assert (start, rel, end) in earlier_patterns and braces | column <= earlier_types[rel], (
                    f"{where}: adds to {rel}, which an earlier version defines; the contract has no group for that")
                continue
            props = found.setdefault(rel, set())
            props |= braces | column
            if version == schema.VERSIONS[0]:
                props |= v10.get(rel, set())
    new_patterns = {p for p in patterns if p[1] not in earlier_types}
    assert new_patterns == set(_group("RELATIONSHIP_PATTERNS", version, ())), _where(version, "Relationships")
    assert {t: frozenset(p) for t, p in found.items()} == dict(_group("RELATIONSHIPS", version, {})), (
        _where(version, "Relationships, properties"))


def test_the_derived_from_label_keys_are_the_v12_table_in_order():
    block = _subsection(_sections()["1.2"], "DERIVED_FROM labels")
    names = tuple(name for row in _table(block) for name in _ticks(row["Property"]))
    assert names == schema.DERIVED_FROM_LABEL_KEYS, "docs/neo4j-schema.md v1.2, DERIVED_FROM labels"


_QUALIFIED = re.compile(r"`([A-Za-z_]\w*)\.([A-Za-z_]\w*)`(?:\s*\(`(\w+)`\))?")


def _bullets(block: str) -> dict[str, str]:
    """The ``- Name: ...`` bullets of a block, continuation lines joined, by name."""
    out, current = {}, None
    for line in block.splitlines():
        match = re.match(r"^- (\w+):(.*)$", line)
        if match:
            current = match.group(1)
            out[current] = match.group(2)
        elif current and line.startswith("  "):
            out[current] += " " + line.strip()
        else:
            current = None
    return out


@pytest.mark.parametrize("version", list(schema.VERSIONS))
def test_the_constraints_and_indexes_are_the_contract(version):
    block = _subsection(_sections()[version], "Constraints and indexes") or ""
    bullets = _bullets(block)
    for bullet, kind in (("Uniqueness", "UNIQUE_CONSTRAINTS"), ("Range", "RANGE_INDEXES")):
        doc = [(label, prop, name) for label, prop, name in _QUALIFIED.findall(bullets.get(bullet, ""))]
        contract = _group(kind, version, ())
        assert [(label, prop) for label, prop, _ in doc] == [(label, prop) for _, label, prop in contract], (
            _where(version, f"Constraints and indexes, {bullet}"))
        for (label, prop, name), (contract_name, _, _) in zip(doc, contract):
            assert not name or name == contract_name, _where(version, f"{bullet}, {label}.{prop}")
    if "Fulltext" in bullets:
        tokens = _ticks(bullets["Fulltext"])
        name = next(t for t in tokens if _IDENT.fullmatch(t))
        on = next(tuple(t.split(".", 1)) for t in tokens if "." in t)
        assert (name, on) == (schema.FULLTEXT_INDEX, schema.FULLTEXT_INDEX_ON), _where(version, "Fulltext")


def test_exactly_one_section_names_the_fulltext_index():
    named = [v for v in schema.VERSIONS
             if "Fulltext" in _bullets(_subsection(_sections()[v], "Constraints and indexes") or "")]
    assert named == [schema.VERSIONS[0]]


# --- the copies ----------------------------------------------------------------------------------------------------
# Each single name below is the contract object itself (an alias), so it is checked by identity.

WRITER_ALIASES = [
    ("writer.SCHEMA_VERSION", lambda: writer.SCHEMA_VERSION, "SCHEMA_VERSION"),
    ("writer.GRAPHMETA_KEYS", lambda: writer.GRAPHMETA_KEYS, "GRAPHMETA_KEYS"),
    ("writer.PLURAL_LABEL_KEYS", lambda: writer.PLURAL_LABEL_KEYS, "DERIVED_FROM_PLURAL_ASSAY_KEYS"),
    ("writer.EDGE_LABEL_KEYS", lambda: writer.EDGE_LABEL_KEYS, "DERIVED_FROM_LABEL_KEYS"),
    ("cypher.EDGE_SINGULAR_ASSAY_KEYS", lambda: cypher.EDGE_SINGULAR_ASSAY_KEYS, "DERIVED_FROM_SINGULAR_ASSAY_KEYS"),
    ("cypher.EDGE_LABEL_KEYS", lambda: cypher.EDGE_LABEL_KEYS, "DERIVED_FROM_LABEL_KEYS"),
    ("cypher.FULLTEXT_INDEX", lambda: cypher.FULLTEXT_INDEX, "FULLTEXT_INDEX"),
    ("labels.SINGULAR_ASSAY_KEYS", lambda: labels.SINGULAR_ASSAY_KEYS, "DERIVED_FROM_SINGULAR_ASSAY_KEYS"),
    ("labels.PLURAL_ASSAY_KEYS", lambda: labels.PLURAL_ASSAY_KEYS, "DERIVED_FROM_PLURAL_ASSAY_KEYS"),
    ("labels.ASSAY_KEYS", lambda: labels.ASSAY_KEYS, "DERIVED_FROM_ASSAY_KEYS"),
    ("labels.PROTOCOL_KEYS", lambda: labels.PROTOCOL_KEYS, "DERIVED_FROM_PROTOCOL_KEYS"),
    ("labels.LABEL_KEYS", lambda: labels.LABEL_KEYS, "DERIVED_FROM_LABEL_KEYS"),
]


@pytest.mark.parametrize("where, read, name", WRITER_ALIASES, ids=[w for w, _, _ in WRITER_ALIASES])
def test_the_writer_copies_are_the_contract(where, read, name):
    assert read() is getattr(schema, name), f"{where} is not schema.{name}"


def test_the_writer_label_rule_is_the_contract():
    assert projection.label_for is schema.type_label


def test_the_writer_composed_names_equal_their_groups():
    assert projection.SYSTEM_KEYS == schema.SAMPLE_SYSTEM_PROPERTIES_V11 | schema.SAMPLE_SYSTEM_PROPERTIES_V12
    assert verify.EXPECTED_CONSTRAINTS == tuple(name for name, _, _ in schema.UNIQUE_CONSTRAINTS_V11)
    assert verify.EXPECTED_INDEXES == (tuple(name for name, _, _ in schema.RANGE_INDEXES_V11)
                                       + (schema.FULLTEXT_INDEX,))


_DDL = re.compile(r"CREATE (CONSTRAINT|INDEX) (\w+) IF NOT EXISTS FOR \((\w+):(\w+)\) "
                  r"(?:REQUIRE \3\.(\w+) IS UNIQUE|ON \(\3\.(\w+)\))$")


def test_the_writer_ddl_creates_the_contract_constraints_and_indexes():
    unique, ranges = [], []
    for statement in cypher.CONSTRAINTS_V11:
        match = _DDL.match(statement)
        assert match, f"cypher.CONSTRAINTS_V11: {statement!r}"
        kind, name, _var, label, unique_prop, range_prop = match.groups()
        (unique if kind == "CONSTRAINT" else ranges).append((name, label, unique_prop or range_prop))
    assert tuple(unique) == schema.UNIQUE_CONSTRAINTS_V11
    assert tuple(ranges) == schema.RANGE_INDEXES_V11
    label, prop = schema.FULLTEXT_INDEX_ON
    assert re.fullmatch(rf"CREATE FULLTEXT INDEX {schema.FULLTEXT_INDEX} IF NOT EXISTS FOR \((\w+):{label}\) "
                        rf"ON EACH \[\1\.{prop}\]", cypher.FULLTEXT)


def _assigned(path: Path, name: str) -> ast.expr:
    """The value of the last assignment to ``name`` anywhere in the file (a try/except fallback included)."""
    found = None
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            found = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            found = node.value
    assert found is not None, f"{path.relative_to(REPO_ROOT)}: no assignment to {name}"
    return found


def _literal(node: ast.expr):
    """A literal, a set literal, ``frozenset(<literal>)``, or a dict of those."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset":
        return frozenset(ast.literal_eval(node.args[0])) if node.args else frozenset()
    if isinstance(node, ast.Dict):
        return {ast.literal_eval(k): _literal(v) for k, v in zip(node.keys, node.values)}
    value = ast.literal_eval(node)
    return frozenset(value) if isinstance(value, set) else value


def test_the_writer_string_names_are_bound_to_the_contract():
    # Python interns a short identifier-like string, so `is` holds for a restated "sample_search_text" too: only the
    # assignment itself shows that the name reads the contract.
    graph_sync = REPO_ROOT / "nextseek_api" / "graph_sync"
    assert ast.unparse(_assigned(graph_sync / "cypher.py", "FULLTEXT_INDEX")) == "schema.FULLTEXT_INDEX"
    assert ast.unparse(_assigned(graph_sync / "writer.py", "SCHEMA_VERSION")) == "schema.SCHEMA_VERSION"


def test_the_graph_search_fulltext_name_is_the_contract():
    path = REPO_ROOT / "nextseek_api" / "graph_search" / "query.py"
    assert _literal(_assigned(path, "FULLTEXT_INDEX")) == schema.FULLTEXT_INDEX


def test_the_fallback_script_system_properties_are_the_sample_groups():
    path = REPO_ROOT / "scripts" / "graph_schema_fallback.py"
    assert _literal(_assigned(path, "V12_SYSTEM_PROPERTIES")) == (
        schema.SAMPLE_SYSTEM_PROPERTIES_V11 | schema.SAMPLE_SYSTEM_PROPERTIES_V12)


# --- the committed fallback capture --------------------------------------------------------------------------------

def test_the_fallback_capture_names_nothing_the_contract_lacks():
    capture = json.loads(CAPTURE_PATH.read_text(encoding="utf-8"))
    where = str(CAPTURE_PATH.relative_to(REPO_ROOT))
    last = schema.VERSIONS[-1]
    relationships: dict[str, frozenset[str]] = {}
    for _, group in _all("RELATIONSHIPS", {}):
        for rel, props in group.items():
            relationships[rel] = relationships.get(rel, frozenset()) | props
    patterns = {p for _, group in _all("RELATIONSHIP_PATTERNS", ()) for p in group}
    assert set(capture["node_labels"]) <= _labels_through(last), f"{where}: node_labels"
    assert set(capture["relationship_types"]) <= set(relationships), f"{where}: relationship_types"
    found = {(p["start"], p["type"], p["end"]) for p in capture["relationship_patterns"]}
    assert found <= patterns, f"{where}: relationship_patterns {found - patterns}"
    for rel, props in capture["relationship_properties"].items():
        assert set(props) <= relationships[rel], f"{where}: relationship_properties.{rel}"
    for label, props in capture["node_properties"].items():
        if label in (schema.SAMPLE, schema.ORPHAN_SAMPLE):
            continue
        allowed = _node_properties_through(last, label) | (
            schema.LEGACY_ATTRIBUTE_STATS if label == schema.ATTRIBUTE else frozenset())
        assert set(props) <= allowed, f"{where}: node_properties.{label} {set(props) - allowed}"
    assert _sample_system_through(last) <= set(capture["node_properties"][schema.SAMPLE]), (
        f"{where}: node_properties.Sample lacks a system property")

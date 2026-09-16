r"""Find every writer site in the tree: the writer inventory's scan, made a gate.

    python3 ci/gate/writer_scan.py      # from the repo root: the sites, one per line

A site is a function (or a .sql file) that writes a graph source table, calls
SEEK's own API to have Rails write one, or writes Cypher. ci/writers.py declares
what tells graph_sync about each, and ci/gate/test_writer_registry.py diffs the
two in both directions, so a writer added anywhere in the tree fails the gate
until somebody says which sync hears about it.

What it recognises, one shape per group of tests in
ci/gate/test_writer_scan_unit.py:

  * SQL in a Python string: INSERT, REPLACE INTO, UPDATE ... SET, DELETE FROM and
    TRUNCATE on a graph source, written plainly, as an f-string or concatenated;
  * the legacy table layer: a class that sets self.tablename, every class it is
    composed of (seek/sample/table.py::DBtable_sample binds eight), and calls of
    storeOneRecord, deleteOneRecord, deleteRecordsConstraint and processRecords
    on self, on a variable made from such a class, or through self.tablemodel;
  * Django ORM writes on the models whose db_table is a graph source, read from
    seek/models/ rather than restated here;
  * SeekAPIClient create_*/update_*/delete_* and run_seek_rails_runner. These
    carry NO table: what Rails commits behind a proxy is declared in
    ci/writers.py, because the route trace corrected two of the guesses a table
    map made (the inventory, section 5);
  * Cypher strings that write (MERGE, CREATE, SET, DELETE, REMOVE);
  * .sql files anywhere under the scanned roots and under docs/.

What it deliberately does not read as a statement: a docstring, a string compiled
or tested as a pattern (re.compile, startswith and their siblings), a read-only
operation of the record layer (processRecords(..., "retrieve")), a SELECT, a
read-only Cypher query. Tests, migrations and NessieAI/history are not scanned at
all.

A write whose table name is built at run time is reported separately, as an
unresolved site, and ci/writers.py lists those too: the generic record layers are
infrastructure that a declared writer calls, not writers of their own, but a NEW
module writing SQL through a variable table name has to be classified before the
gate goes green again.

Standard library only, like every module under ci/.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ci.writers import GRAPH_SOURCE_TABLES  # noqa: E402

# Everything the application, its tools and its installer hold. docs/ is scanned
# for .sql only: the publication rollout's hand SQL is archived there.
SCAN_ROOTS = ("nextseek_api", "seek", "dmac", "api_app", "NessieAI", "scripts", "startup", "ci", "docker")
SQL_ROOTS = ("docs",)
SKIP_PARTS = frozenset({".git", ".venv", "__pycache__", "node_modules", "migrations", "history", "baml_client"})

UNRESOLVED_KIND = "dynamic"      # a write whose table name the scan cannot resolve
UNPARSED_KIND = "unparsed"       # a module the scan cannot parse, so cannot clear
UNRESOLVED_KINDS = (UNRESOLVED_KIND, UNPARSED_KIND)
WRITE_KINDS = ("sql", "sql_file", "orm", "dbtable", "seek_client", "rails_runner", "cypher")

WRITE_OPS = frozenset({"create", "update", "delete"})

# Legacy record-layer methods that write. processRecords is both a reader and a
# writer, so its operation comes from the call's own string arguments.
DBTABLE_WRITE_METHODS = {"storeOneRecord", "deleteOneRecord", "deleteRecordsConstraint", "processRecords"}
ORM_WRITE_METHODS = {"create", "update", "delete", "bulk_create", "bulk_update", "get_or_create", "update_or_create"}
# SEEK resources a proxy can write through SeekAPIClient. A vocabulary of names,
# never a table map: the tables are ci/writers.py's to declare.
SEEK_RESOURCES = frozenset({"sample", "sample_type", "project", "person", "people", "investigation", "study",
                            "assay", "sop", "data_file", "document", "publication"})
_SEEK_CLIENT_METHOD = re.compile(r"^(create|update|delete)_(\w+)$")

# Calls whose string arguments are patterns or prefixes, not statements.
TEXT_PREDICATES = frozenset({"compile", "search", "match", "fullmatch", "sub", "subn", "findall", "finditer",
                             "split", "startswith", "endswith", "strip", "lstrip", "rstrip"})

_OP_OF_METHOD = {
    "create": "create", "bulk_create": "create", "get_or_create": "create", "update_or_create": "update",
    "update": "update", "bulk_update": "update", "delete": "delete", "save": "update",
    "storeOneRecord": "update", "deleteOneRecord": "delete", "deleteRecordsConstraint": "delete",
}
_OP_OF_RECORD_ARG = {"save": "update", "store": "create", "new": "create", "update": "update", "delete": "delete"}

# A table name: optionally schema-qualified, optionally backticked or quoted, and
# possibly an f-string placeholder or a parameter marker rather than a name.
_T = r"[`\"]?(?:(?P<schema>\w+)[`\"]?\.)?[`\"]?(?P<table>\w+|\{[^}]*\}|%s)[`\"]?"
SQL_PATTERNS = (
    ("create", re.compile(r"\b(?:INSERT(?:\s+IGNORE)?\s+INTO|REPLACE\s+INTO)\s+" + _T, re.I)),
    ("update", re.compile(r"\bUPDATE\s+(?:IGNORE\s+)?" + _T + r"(?=[^;]{0,600}?\bSET\b)", re.I | re.S)),
    ("delete", re.compile(r"\bDELETE\s+(?:\w+\s+)?FROM\s+" + _T, re.I)),
    ("delete", re.compile(r"\bTRUNCATE\s+(?:TABLE\s+)?" + _T, re.I)),
)
CYPHER_WRITE = re.compile(r"\b(MERGE|CREATE|DETACH\s+DELETE|DELETE|SET|REMOVE)\b")
CYPHER_MARK = re.compile(r"(\(\s*\w*\s*:\s*[`$\w(]|\bMATCH\b|\bUNWIND\b|\bMERGE\b|-\[)")
SQL_MARK = re.compile(r"\bUPDATE\s+\S+.*\bSET\b|\bDELETE\s+FROM\b|\bINSERT\s+INTO\b|\bSELECT\b[^;]*\bFROM\b",
                      re.I | re.S)
_SQL_COMMENT = re.compile(r"--[^\n]*")
_SQL_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)


@dataclass(frozen=True)
class Finding:
    """One write the scan saw, at one line."""

    kind: str
    site: str
    file: str
    line: int
    tables: tuple[str, ...]
    ops: tuple[str, ...]
    snippet: str


@dataclass(frozen=True)
class Site:
    """Every finding of one site, folded together. This is what the gate diffs."""

    site: str
    file: str
    kinds: tuple[str, ...]
    tables: tuple[str, ...]
    ops: tuple[str, ...]
    lines: tuple[int, ...]


def is_scanned(rel: str) -> bool:
    """Whether the scan reads this repo-relative path at all."""
    parts = rel.split("/")
    if not parts or parts[0] not in (SCAN_ROOTS + SQL_ROOTS if rel.endswith(".sql") else SCAN_ROOTS):
        return False
    if SKIP_PARTS.intersection(parts) or "tests" in parts or "test" in parts:
        return False
    name = parts[-1]
    return not (name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py")


@lru_cache(maxsize=None)
def orm_models(root: str = str(ROOT)) -> tuple[tuple[str, str], ...]:
    """(model class, table) for every seek/ model whose db_table is a graph source.

    Read from the models rather than restated here, so a new mirror model of a
    graph source is scanned the day it lands.
    """
    out: dict[str, str] = {}
    models_dir = Path(root) / "seek" / "models"
    for path in sorted(models_dir.glob("*.py")) if models_dir.is_dir() else []:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Assign) and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Name) and sub.targets[0].id == "db_table"
                        and isinstance(sub.value, ast.Constant) and sub.value.value in GRAPH_SOURCE_TABLES):
                    out[node.name] = sub.value.value
    return tuple(sorted(out.items()))


def tables_by_class(sources: dict[str, str]) -> dict[str, str]:
    """Legacy table classes bound to a table name, and every class they are composed of.

    A mixin holds the write and the concrete class holds the name, so a mixin
    inherits the binding of the class that mixes it in.
    """
    declared: dict[str, str] = {}
    bases_of: dict[str, list[str]] = {}
    for rel, source in sources.items():
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases_of[node.name] = [ast.unparse(b).split(".")[-1] for b in node.bases]
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Assign) and len(sub.targets) == 1
                        and isinstance(sub.targets[0], ast.Attribute) and sub.targets[0].attr == "tablename"
                        and isinstance(sub.value, ast.Constant) and isinstance(sub.value.value, str)
                        and sub.value.value):
                    declared[node.name] = sub.value.value
    bound = dict(declared)
    for name, table in declared.items():
        for base in bases_of.get(name, []):
            if base not in ("DBtable", "object"):
                bound.setdefault(base, table)
    return bound


class _Scanner(ast.NodeVisitor):
    """One module's writes. The site is the innermost function, or '<module>'."""

    def __init__(self, rel: str, by_class: dict[str, str], models: dict[str, str]):
        self.rel = rel
        self.by_class = by_class
        self.models = models
        self.stack: list[str] = []
        self.class_stack: list[str] = []
        self.findings: list[Finding] = []
        self.not_statements: set[int] = set()
        self.bound_vars: list[dict[str, tuple[str, str]]] = [{}]

    # --- bookkeeping ---
    def site(self) -> str:
        return f"{self.rel}::{'.'.join(self.stack) or '<module>'}"

    def add(self, kind: str, node, tables, ops, snippet: str) -> None:
        self.findings.append(Finding(kind=kind, site=self.site(), file=self.rel,
                                     line=getattr(node, "lineno", 0), tables=tuple(sorted(set(tables))),
                                     ops=tuple(sorted(set(ops))), snippet=snippet[:200]))

    def _mark_docstring(self, node) -> None:
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            self.not_statements.add(id(body[0].value))

    # --- scopes ---
    def visit_Module(self, node):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        self._mark_docstring(node)
        self.stack.append(node.name)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()
        self.stack.pop()

    def _function(self, node):
        self._mark_docstring(node)
        self.stack.append(node.name)
        self.bound_vars.append({})
        self.generic_visit(node)
        self.bound_vars.pop()
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    # --- variables bound to a table ---
    def visit_Assign(self, node):
        bound = self._bound_table(node.value)
        if bound:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.bound_vars[-1][target.id] = bound
                elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                        and target.value.id == "self":
                    self.bound_vars[-1]["self." + target.attr] = bound
        self.generic_visit(node)

    def visit_For(self, node):
        bound = self._bound_table(node.iter)
        if bound and isinstance(node.target, ast.Name):
            self.bound_vars[-1][node.target.id] = bound
        self.generic_visit(node)

    def _bound_table(self, expr) -> tuple[str, str] | None:
        """(table, 'dbtable' | 'orm') an expression's value is bound to."""
        for sub in ast.walk(expr):
            if isinstance(sub, ast.Attribute) and sub.attr == "tablemodel" and isinstance(sub.value, ast.Name) \
                    and sub.value.id == "self" and self._self_table():
                return (self._self_table(), "orm")
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                if sub.func.id in self.by_class:
                    return (self.by_class[sub.func.id], "dbtable")
                if sub.func.id in self.models:
                    return (self.models[sub.func.id], "orm")
            if isinstance(sub, ast.Attribute) and sub.attr == "objects" and isinstance(sub.value, ast.Name) \
                    and sub.value.id in self.models:
                return (self.models[sub.value.id], "orm")
        return None

    def _self_table(self) -> str | None:
        return self.by_class.get(self.class_stack[-1]) if self.class_stack else None

    def _lookup(self, name: str, kind: str) -> str | None:
        for scope in reversed(self.bound_vars):
            if name in scope:
                table, bound_kind = scope[name]
                return table if bound_kind == kind else None
        return None

    # --- calls ---
    def visit_Call(self, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else "")
        if name in TEXT_PREDICATES:
            # A pattern or a prefix, not a statement: mark every string inside the
            # call so the visitors below leave it alone. The seed loader's parser
            # and Nessie's read-only guard are both this shape.
            for argument in list(node.args) + [kw.value for kw in node.keywords]:
                for sub in ast.walk(argument):
                    self.not_statements.add(id(sub))
        if isinstance(func, ast.Attribute):
            self._orm_call(node, func, name)
            self._dbtable_call(node, func, name)
            self._seek_client_call(node, func, name)
        if name == "run_seek_rails_runner":
            self.add("rails_runner", node, (), ("create", "update"), ast.unparse(node))
        self.generic_visit(node)

    def _orm_call(self, node, func, meth: str) -> None:
        if meth == "save" and isinstance(func.value, ast.Name):
            table = self._lookup(func.value.id, "orm")
            if table:
                self._orm_finding(node, table, "update")
            return
        if meth not in ORM_WRITE_METHODS:
            return
        root, has_objects, via_tablemodel = func.value, False, False
        while isinstance(root, (ast.Attribute, ast.Call)):
            if isinstance(root, ast.Attribute) and root.attr == "objects":
                has_objects = True
            if isinstance(root, ast.Attribute) and root.attr == "tablemodel":
                via_tablemodel = True
            root = root.func if isinstance(root, ast.Call) else root.value
        if has_objects and via_tablemodel and isinstance(root, ast.Name) and root.id == "self" \
                and self._self_table():
            self._orm_finding(node, self._self_table(), _OP_OF_METHOD[meth])
        elif has_objects and isinstance(root, ast.Name) and root.id in self.models:
            self._orm_finding(node, self.models[root.id], _OP_OF_METHOD[meth])
        elif isinstance(root, ast.Name) and meth in {"update", "delete"} and not has_objects:
            table = self._lookup(root.id, "orm")
            if table:
                self._orm_finding(node, table, _OP_OF_METHOD[meth])

    def _orm_finding(self, node, table: str, op: str) -> None:
        if table in GRAPH_SOURCE_TABLES:
            self.add("orm", node, (table,), (op,), ast.unparse(node))

    def _dbtable_call(self, node, func, meth: str) -> None:
        if meth not in DBTABLE_WRITE_METHODS:
            return
        target, table = func.value, None
        if isinstance(target, ast.Name) and target.id == "self":
            table = self._self_table()
        elif isinstance(target, ast.Name):
            table = self._lookup(target.id, "dbtable")
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) \
                and target.value.id == "self":
            table = self._lookup("self." + target.attr, "dbtable")
        elif isinstance(target, ast.Call) and isinstance(target.func, ast.Name):
            table = self.by_class.get(target.func.id)
        ops = self._record_ops(meth, node)
        if not WRITE_OPS.intersection(ops):
            return            # a read through the record layer, such as processRecords(..., "retrieve")
        if table is None:
            self.add(UNRESOLVED_KIND, node, (), ops, ast.unparse(node))
        elif table in GRAPH_SOURCE_TABLES:
            self.add("dbtable", node, (table,), ops, ast.unparse(node))

    @staticmethod
    def _record_ops(meth: str, node) -> tuple[str, ...]:
        if meth != "processRecords":
            if meth == "storeOneRecord":
                return ("create", "update")
            return (_OP_OF_METHOD[meth],)
        named = {_OP_OF_RECORD_ARG.get(a.value, a.value) for a in node.args[1:]
                 if isinstance(a, ast.Constant) and isinstance(a.value, str)}
        return tuple(sorted(named)) or ("create", "update", "delete")

    def _seek_client_call(self, node, func, meth: str) -> None:
        match = _SEEK_CLIENT_METHOD.match(meth)
        if not match or match.group(2) not in SEEK_RESOURCES:
            return
        holder = ast.unparse(func.value).lower()
        if "client" not in holder and "seek" not in holder:
            return
        # No table: what Rails commits is DECLARED in ci/writers.py, never inferred.
        self.add("seek_client", node, (), (match.group(1),), ast.unparse(node))

    # --- strings ---
    def visit_Constant(self, node):
        if isinstance(node.value, str) and id(node) not in self.not_statements:
            self._check_text(node, node.value)

    def visit_JoinedStr(self, node):
        if id(node) in self.not_statements:
            return
        self._check_text(node, _joined_text(node))
        # Deliberately not descending: the constant parts were read as one string.

    def visit_BinOp(self, node):
        if isinstance(node.op, ast.Add) and id(node) not in self.not_statements:
            text = _concat_text(node)
            if text is not None:
                self._check_text(node, text)
                return
        self.generic_visit(node)

    def _check_text(self, node, text: str) -> None:
        if len(text) < 8:
            return
        matched_sql = False
        for op, pattern in SQL_PATTERNS:
            for match in pattern.finditer(text):
                matched_sql = True
                table = match.group("table")
                snippet = text[max(0, match.start() - 20): match.end() + 60].replace("\n", " ")
                if table.startswith("{") or table == "%s":
                    resolved = None
                    if any(key in table for key in ("tablename", "fulltablename", "tablemodel")):
                        resolved = self._self_table()
                    if resolved in GRAPH_SOURCE_TABLES:
                        self.add("sql", node, (resolved,), (op,), snippet)
                    else:
                        self.add(UNRESOLVED_KIND, node, (), (op,), snippet)
                elif table.lower() in GRAPH_SOURCE_TABLES:
                    self.add("sql", node, (table.lower(),), (op,), snippet)
        if matched_sql:
            return
        if CYPHER_WRITE.search(text) and CYPHER_MARK.search(text) and not SQL_MARK.search(text):
            ops = {" ".join(w.split()).lower() for w in CYPHER_WRITE.findall(text)}
            self.add("cypher", node, (), ops, re.sub(r"\s+", " ", text))


def _joined_text(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
        else:
            parts.append("{" + ast.unparse(value.value) + "}")
    return "".join(parts)


def _concat_text(node) -> str | None:
    """The text of a '+' chain of string literals, with non-literal parts as placeholders."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return _joined_text(node)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _concat_text(node.left), _concat_text(node.right)
        if left is None and right is None:
            return None
        return ((left if left is not None else "{" + ast.unparse(node.left) + "}")
                + (right if right is not None else "{" + ast.unparse(node.right) + "}"))
    return None


def scan_module(rel: str, source: str, *, tables_by_class: dict[str, str] | None = None,
                models_by_class: dict[str, str] | None = None) -> list[Finding]:
    """Every write one Python module holds, in line order.

    `tables_by_class` is the whole tree's legacy-table binding (a mixin's write is
    resolved by the class that mixes it in), and defaults to none of them.
    `models_by_class` defaults to the models under seek/models/.
    """
    models = dict(orm_models()) if models_by_class is None else models_by_class
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return [Finding(kind=UNPARSED_KIND, site=rel, file=rel, line=0, tables=(), ops=(), snippet=str(exc)[:200])]
    scanner = _Scanner(rel, tables_by_class or {}, models)
    scanner.visit(tree)
    return sorted(scanner.findings, key=lambda f: (f.line, f.kind))


def scan_sql_text(rel: str, text: str) -> list[Finding]:
    """Every write one .sql file holds. Its site is the file itself."""
    body = _SQL_BLOCK_COMMENT.sub("", _SQL_COMMENT.sub("", text))
    out: list[Finding] = []
    for op, pattern in SQL_PATTERNS:
        for match in pattern.finditer(body):
            table = (match.group("table") or "").lower()
            if table in GRAPH_SOURCE_TABLES:
                out.append(Finding(kind="sql_file", site=rel, file=rel, line=body.count("\n", 0, match.start()) + 1,
                                   tables=(table,), ops=(op,),
                                   snippet=body[match.start(): match.end() + 60].replace("\n", " ")[:200]))
    return sorted(out, key=lambda f: f.line)


@lru_cache(maxsize=None)
def _sources(root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Every scanned Python module and .sql file, read once, keyed by repo-relative path."""
    python: dict[str, str] = {}
    sql: dict[str, str] = {}
    for base in SCAN_ROOTS + SQL_ROOTS:
        top = root / base
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*")):
            if not path.is_file() or path.suffix not in (".py", ".sql"):
                continue
            rel = path.relative_to(root).as_posix()
            if not is_scanned(rel):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            (python if path.suffix == ".py" else sql)[rel] = text
    return python, sql


@lru_cache(maxsize=None)
def _scan_tree(root: str) -> tuple[tuple[Site, ...], tuple[Site, ...]]:
    python, sql = _sources(Path(root))
    by_class = tables_by_class(python)
    findings: list[Finding] = []
    for rel, source in python.items():
        findings += scan_module(rel, source, tables_by_class=by_class)
    for rel, text in sql.items():
        findings += scan_sql_text(rel, text)
    folded: dict[str, dict] = {}
    for finding in findings:
        entry = folded.setdefault(finding.site, {"file": finding.file, "kinds": set(), "tables": set(),
                                                 "ops": set(), "lines": set()})
        entry["kinds"].add(finding.kind)
        entry["tables"].update(finding.tables)
        entry["ops"].update(finding.ops)
        entry["lines"].add(finding.line)
    writers, unresolved = [], []
    for site in sorted(folded):
        entry = folded[site]
        made = Site(site=site, file=entry["file"], kinds=tuple(sorted(entry["kinds"])),
                    tables=tuple(sorted(entry["tables"])), ops=tuple(sorted(entry["ops"])),
                    lines=tuple(sorted(entry["lines"])))
        (unresolved if set(made.kinds) <= set(UNRESOLVED_KINDS) else writers).append(made)
    return tuple(writers), tuple(unresolved)


def scan(root: Path = ROOT) -> dict[str, Site]:
    """Every writer site in the tree, by site."""
    return {s.site: s for s in _scan_tree(str(root))[0]}


def unresolved(root: Path = ROOT) -> dict[str, Site]:
    """Every site the scan can see writing but cannot clear, by site."""
    return {s.site: s for s in _scan_tree(str(root))[1]}


def calls_in_source(source: str, symbol: str) -> tuple[str, ...] | None:
    """The names this function calls, or None when the module holds no such function.

    'symbol' is the dotted path the scan reports: 'Class.method', 'function', or a
    nested 'outer.inner'. This is what proves a declared hook is really called in
    the function ci/writers.py names.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    wanted = symbol.split(".")
    found: list[ast.AST] = []

    def walk(node, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = stack + [child.name]
                if here == wanted:
                    found.append(child)
                elif here == wanted[:len(here)]:
                    walk(child, here)
            else:
                walk(child, stack)

    if symbol == "<module>":
        found.append(tree)
    else:
        walk(tree, [])
    if not found:
        return None
    names = set()
    for node in found:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                names.add(ast.unparse(sub.func))
    return tuple(sorted(names))


def calls_in(root: Path, site: str) -> tuple[str, ...] | None:
    """calls_in_source for a 'path::symbol' site of the tree."""
    path, _, symbol = site.partition("::")
    file = root / path
    if not file.is_file():
        return None
    return calls_in_source(file.read_text(encoding="utf-8"), symbol or "<module>")


def definitions_in_source(source: str, name: str) -> tuple[str, ...]:
    """The dotted symbol of every function of this name in one module, outermost first."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return ()
    out: list[str] = []

    def walk(node, stack: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                here = stack + [child.name]
                if child.name == name and not isinstance(child, ast.ClassDef):
                    out.append(".".join(here))
                walk(child, here)
            else:
                walk(child, stack)

    walk(tree, [])
    return tuple(out)


@lru_cache(maxsize=None)
def defined_at(name: str, root: Path = ROOT) -> tuple[str, ...]:
    """Every 'path::symbol' in the scanned tree that defines a function of this name.

    A writer may enqueue through a helper rather than calling hooks.enqueue itself.
    This is what lets the gate follow the helper to its definition and check that it
    still reaches the hook.
    """
    out: list[str] = []
    for rel, source in _sources(root)[0].items():
        out += [f"{rel}::{symbol}" for symbol in definitions_in_source(source, name)]
    return tuple(sorted(out))


def main(root: Path = ROOT) -> int:
    print("site\tkinds\ttables\tops")
    for site in scan(root).values():
        print(f"{site.site}\t{','.join(site.kinds)}\t{','.join(site.tables)}\t{','.join(site.ops)}")
    print("\nunresolved (the table name is built at run time)")
    for site in unresolved(root).values():
        print(f"{site.site}\t{','.join(site.kinds)}\t{','.join(site.ops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Source-derived evidence helpers shared by Plan 018 V4 verifiers."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


@dataclass(frozen=True)
class MigrationGraph:
    """The local nextseek_api migration graph, parsed without Django settings."""

    dependencies: dict[str, frozenset[str]]
    leaves: tuple[str, ...]

    def ancestors_of(self, migration: str) -> frozenset[str]:
        seen: set[str] = set()
        pending = [migration]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            pending.extend(self.dependencies.get(current, ()))
        return frozenset(seen)


@dataclass(frozen=True)
class JUnitSummary:
    tests: int
    failures: int
    errors: int
    skipped: int
    suites: int


def _migration_dependencies(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Migration":
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or not any(
                isinstance(target, ast.Name) and target.id == "dependencies"
                for target in statement.targets
            ):
                continue
            if not isinstance(statement.value, (ast.List, ast.Tuple)):
                raise ValueError(f"Migration.dependencies is not a list in {path}")
            dependencies: set[str] = set()
            for item in statement.value.elts:
                if not isinstance(item, (ast.Tuple, ast.List)) or len(item.elts) != 2:
                    continue
                app_label, dependency = item.elts
                if not (
                    isinstance(app_label, ast.Constant)
                    and app_label.value == "nextseek_api"
                    and isinstance(dependency, ast.Constant)
                    and isinstance(dependency.value, str)
                ):
                    continue
                dependencies.add(dependency.value)
            return frozenset(dependencies)
    raise ValueError(f"Migration.dependencies is missing from {path}")


def derive_migration_graph(migrations_dir: Path) -> MigrationGraph:
    dependencies = {
        path.stem: _migration_dependencies(path)
        for path in sorted(migrations_dir.glob("[0-9][0-9][0-9][0-9]_*.py"))
    }
    if not dependencies:
        raise ValueError(f"No migration sources found in {migrations_dir}")
    referenced = set().union(*dependencies.values())
    leaves = tuple(sorted(set(dependencies) - referenced))
    return MigrationGraph(dependencies=dependencies, leaves=leaves)


def summarize_junit(path: Path) -> JUnitSummary:
    root = ElementTree.parse(path).getroot()
    suites = [
        suite
        for suite in root.iter("testsuite")
        if not any(child.tag == "testsuite" for child in suite)
    ]

    def total(attribute: str) -> int:
        return sum(int(suite.attrib.get(attribute, "0")) for suite in suites)

    return JUnitSummary(
        tests=total("tests"),
        failures=total("failures"),
        errors=total("errors"),
        skipped=total("skipped"),
        suites=len(suites),
    )

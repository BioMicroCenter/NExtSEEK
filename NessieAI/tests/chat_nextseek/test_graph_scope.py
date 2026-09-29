"""
GraphScope: who is asking a graph question, carried as plain data on a per-request config copy.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 4.1 and 4.3. The scope is
fail-closed: no scope, or anything that is not a GraphScope, reads as None, and None refuses at the
tool and redacts the catalog. An empty project set is a real scope that sees nothing. Only an
explicit "1" in CHAT_NEXTSEEK_GRAPH_ADMIN makes a single-operator surface an admin.
"""
from __future__ import annotations

import copy
import dataclasses
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek import graph_scope as gs
from chat_nextseek.graph_scope import (
    HIDDEN_SAMPLE_PROPERTIES,
    OPERATOR_OPT_IN_ENV,
    RESERVED_PREFIX,
    SCOPE_ATTR,
    SCOPE_PARAM,
    GraphScope,
    operator_scope_from_env,
    scope_of,
    sees_all,
    with_scope,
)


def test_constants_are_the_spec_values():
    assert SCOPE_ATTR == "GRAPH_SCOPE"
    assert SCOPE_PARAM == "__scope_projects"
    assert RESERVED_PREFIX == "__scope"
    assert SCOPE_PARAM.startswith(RESERVED_PREFIX)
    assert OPERATOR_OPT_IN_ENV == "CHAT_NEXTSEEK_GRAPH_ADMIN"
    assert HIDDEN_SAMPLE_PROPERTIES == frozenset({"parent_titles", "parent_title_hashes"})


# --------------------------------------------------------------------------- #
# The type
# --------------------------------------------------------------------------- #

def test_graph_scope_is_frozen():
    scope = GraphScope.for_projects([1])
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.is_admin = True  # type: ignore[misc]


def test_admin_constructor():
    scope = GraphScope.admin("cli")
    assert scope.is_admin is True
    assert scope.project_ids == ()
    assert scope.source == "cli"


def test_for_projects_sorts_and_deduplicates():
    scope = GraphScope.for_projects([13, 2, 13, 2])
    assert scope.is_admin is False
    assert scope.project_ids == (2, 13)
    assert scope.source == "request"


def test_for_projects_empty_is_a_real_scope_that_sees_nothing():
    scope = GraphScope.for_projects([])
    assert scope.is_admin is False
    assert scope.project_ids == ()


@pytest.mark.parametrize("bad", [[True], ["1"], [1.0], [None], "12", None, 5])
def test_for_projects_rejects_anything_but_real_ints(bad):
    with pytest.raises(ValueError):
        GraphScope.for_projects(bad)


def test_direct_construction_validates_and_normalises():
    assert GraphScope(is_admin=False, project_ids=(3, 1, 3)).project_ids == (1, 3)
    assert GraphScope(is_admin=True, project_ids=(3,)).project_ids == ()
    with pytest.raises(ValueError):
        GraphScope(is_admin=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GraphScope(is_admin=False, project_ids=(True,))
    with pytest.raises(ValueError):
        GraphScope(is_admin=False, source=None)  # type: ignore[arg-type]


def test_from_plain_non_admin():
    scope = GraphScope.from_plain({"is_admin": False, "project_ids": [13, 2, 2]})
    assert scope == GraphScope(is_admin=False, project_ids=(2, 13), source="request")


def test_from_plain_admin_drops_ids():
    scope = GraphScope.from_plain({"is_admin": True, "project_ids": [1, 2]}, source="evaluator")
    assert scope.is_admin is True
    assert scope.project_ids == ()
    assert scope.source == "evaluator"


def test_from_plain_missing_ids_is_the_empty_set():
    assert GraphScope.from_plain({"is_admin": False}).project_ids == ()


@pytest.mark.parametrize(
    "data",
    [
        {"is_admin": 1, "project_ids": [1]},
        {"is_admin": "true", "project_ids": [1]},
        {"is_admin": None, "project_ids": [1]},
        {"project_ids": [1]},
        {"is_admin": False, "project_ids": [True]},
        {"is_admin": False, "project_ids": ["2"]},
        {"is_admin": False, "project_ids": [2.0]},
        {"is_admin": False, "project_ids": "2"},
        {"is_admin": False, "project_ids": {"2": 2}},
        {"is_admin": False, "project_ids": None},
        [("is_admin", False)],
        "admin",
        None,
    ],
)
def test_from_plain_rejects_malformed_values(data):
    with pytest.raises(ValueError):
        GraphScope.from_plain(data)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# scope_of / with_scope / sees_all
# --------------------------------------------------------------------------- #

def test_scope_of_absent_is_none():
    assert scope_of(SimpleNamespace()) is None


@pytest.mark.parametrize("value", [None, {"is_admin": True}, "admin", True, 1, MagicMock()])
def test_scope_of_anything_but_a_graph_scope_is_none(value):
    config = SimpleNamespace(**{SCOPE_ATTR: value})
    assert scope_of(config) is None


def test_scope_of_a_magicmock_config_is_none():
    assert scope_of(MagicMock()) is None


def test_scope_of_a_dict_config_is_none():
    assert scope_of({SCOPE_ATTR: GraphScope.admin("test")}) is None


def test_scope_of_a_property_that_raises_is_none():
    class Broken:
        @property
        def GRAPH_SCOPE(self):  # noqa: N802
            raise RuntimeError("boom")

    assert scope_of(Broken()) is None


def test_with_scope_copies_and_never_mutates():
    original = SimpleNamespace(API_USER="someone")
    scope = GraphScope.for_projects([2])
    scoped = with_scope(original, scope)
    assert scoped is not original
    assert scope_of(scoped) is scope
    assert not hasattr(original, SCOPE_ATTR)
    assert scoped.API_USER == "someone"


def test_with_scope_replaces_an_existing_scope_on_the_copy_only():
    original = with_scope(SimpleNamespace(), GraphScope.admin("test"))
    narrowed = with_scope(original, GraphScope.for_projects([1]))
    assert scope_of(original).is_admin is True
    assert scope_of(narrowed).is_admin is False


def test_with_scope_none_is_stored_and_refuses():
    scoped = with_scope(SimpleNamespace(), None)
    assert hasattr(scoped, SCOPE_ATTR)
    assert getattr(scoped, SCOPE_ATTR) is None
    assert scope_of(scoped) is None


def test_with_scope_stores_none_for_anything_but_a_graph_scope():
    scoped = with_scope(SimpleNamespace(), {"is_admin": True})  # type: ignore[arg-type]
    assert getattr(scoped, SCOPE_ATTR) is None


def test_scope_survives_a_later_shallow_copy():
    scoped = with_scope(SimpleNamespace(), GraphScope.for_projects([4]))
    again = copy.copy(scoped)
    assert scope_of(again) == GraphScope.for_projects([4])


def test_sees_all_only_for_admin():
    assert sees_all(with_scope(SimpleNamespace(), GraphScope.admin("test"))) is True
    assert sees_all(with_scope(SimpleNamespace(), GraphScope.for_projects([1, 2]))) is False
    assert sees_all(with_scope(SimpleNamespace(), GraphScope.for_projects([]))) is False
    assert sees_all(with_scope(SimpleNamespace(), None)) is False
    assert sees_all(SimpleNamespace()) is False
    assert sees_all(MagicMock()) is False
    assert sees_all(None) is False


# --------------------------------------------------------------------------- #
# The single-operator opt-in
# --------------------------------------------------------------------------- #

def test_operator_scope_needs_exactly_one():
    assert operator_scope_from_env("cli", {OPERATOR_OPT_IN_ENV: "1"}) == GraphScope.admin("cli")


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1", "1 ", "01", "TRUE"])
def test_operator_scope_anything_else_is_none(value):
    assert operator_scope_from_env("mcp", {OPERATOR_OPT_IN_ENV: value}) is None


def test_operator_scope_unset_is_none():
    assert operator_scope_from_env("app", {}) is None


def test_operator_scope_reads_the_process_environment(monkeypatch):
    monkeypatch.delenv(OPERATOR_OPT_IN_ENV, raising=False)
    assert operator_scope_from_env("evaluator") is None
    monkeypatch.setenv(OPERATOR_OPT_IN_ENV, "1")
    assert operator_scope_from_env("evaluator") == GraphScope.admin("evaluator")


def test_module_imports_nothing_from_the_engine_or_django():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(gs))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    for name in imported:
        assert not name.startswith("."), name
        assert not name.startswith(("django", "nextseek_api", "neo4j", "chat_nextseek")), name

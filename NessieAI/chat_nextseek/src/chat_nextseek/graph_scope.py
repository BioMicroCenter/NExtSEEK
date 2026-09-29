"""
Who is asking a graph question: an unscoped admin, or a caller limited to a set of projects.

The scope rides on a per-request ChatConfig copy as plain data (``GRAPH_SCOPE``). The server resolves it from the
caller's account (``nextseek_api/graph_search/scope.py``) and hands it down; this package never imports the API side.
Everything here fails closed:

- no scope, or anything that is not a ``GraphScope`` (a MagicMock config, a dict, a string), reads as ``None``, and
  ``None`` refuses at the Neo4j tool and redacts the catalog;
- an empty project set is a real scope that sees nothing;
- a single-operator surface (the CLI, the MCP server, the evaluator) is an admin only when the process environment
  sets ``CHAT_NEXTSEEK_GRAPH_ADMIN`` to exactly ``"1"``; nothing a served process runs reads that variable.

The Cypher prover (``cypher_scope.py``) binds the caller's project ids as the one parameter ``__scope_projects`` and
names everything it generates with the ``__scope`` prefix, so both are reserved: a caller-supplied parameter or name
with that prefix is refused, never merged.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 4.
"""
from __future__ import annotations

import copy
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

SCOPE_ATTR = "GRAPH_SCOPE"            # the attribute on a per-request ChatConfig copy
SCOPE_PARAM = "__scope_projects"      # the one parameter the server binds; reserved
RESERVED_PREFIX = "__scope"           # every generated name; reserved in model text and parameter keys
OPERATOR_OPT_IN_ENV = "CHAT_NEXTSEEK_GRAPH_ADMIN"
#: Sample properties computed from a parent's own metadata, which may belong to a project the caller cannot see.
HIDDEN_SAMPLE_PROPERTIES = frozenset({"parent_titles", "parent_title_hashes"})


def _real_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _project_tuple(project_ids: Any) -> tuple[int, ...]:
    """Sorted, de-duplicated ids; ValueError unless a real collection of real ints (a str or a mapping is not)."""
    if isinstance(project_ids, (str, bytes, Mapping)) or not isinstance(project_ids, Iterable):
        raise ValueError("project_ids must be a list of integers")
    ids = list(project_ids)
    if not all(_real_int(i) for i in ids):
        raise ValueError("every project id must be an integer")
    return tuple(sorted(set(ids)))


@dataclass(frozen=True)
class GraphScope:
    """Who is asking: an unscoped admin, or a caller limited to project_ids. Empty project_ids sees nothing."""

    is_admin: bool
    project_ids: tuple[int, ...] = ()
    source: str = "request"           # "request", "cli", "mcp", "app", "evaluator", "venue-check", "test"

    def __post_init__(self) -> None:
        if not isinstance(self.is_admin, bool):
            raise ValueError("is_admin must be a bool")
        if not isinstance(self.source, str):
            raise ValueError("source must be a string")
        ids = () if self.is_admin else _project_tuple(self.project_ids)
        object.__setattr__(self, "project_ids", ids)

    @classmethod
    def admin(cls, source: str) -> "GraphScope":
        return cls(is_admin=True, project_ids=(), source=source)

    @classmethod
    def for_projects(cls, project_ids: Iterable[int], source: str = "request") -> "GraphScope":
        return cls(is_admin=False, project_ids=_project_tuple(project_ids), source=source)

    @classmethod
    def from_plain(cls, data: Mapping[str, Any], source: str = "request") -> "GraphScope":
        """{"is_admin": bool, "project_ids": [int, ...]} as resolve_scope's caller builds it.

        Raises ValueError unless is_admin is a real bool and every id a real int (not a bool); an admin's ids are
        dropped; ids are sorted and de-duplicated. A missing project_ids is the empty set.
        """
        if not isinstance(data, Mapping):
            raise ValueError("a scope must be a mapping")
        is_admin = data.get("is_admin")
        if not isinstance(is_admin, bool):
            raise ValueError("is_admin must be a bool")
        if is_admin:
            return cls.admin(source)
        return cls.for_projects(data.get("project_ids", ()), source=source)


def scope_of(config: Any) -> GraphScope | None:
    """The GraphScope on this config, or None when the attribute is absent or is anything but a GraphScope
    (a MagicMock config, a dict, a string). None means refuse."""
    try:
        value = getattr(config, SCOPE_ATTR, None)
    except Exception:
        return None
    return value if isinstance(value, GraphScope) else None


def with_scope(config: Any, scope: GraphScope | None) -> Any:
    """A shallow copy of config carrying scope. Never mutates config. None (or anything but a GraphScope) is stored
    as None, which refuses."""
    scoped = copy.copy(config)
    setattr(scoped, SCOPE_ATTR, scope if isinstance(scope, GraphScope) else None)
    return scoped


def sees_all(config: Any) -> bool:
    """True only when scope_of(config) is an admin scope. The catalog's redaction switch."""
    scope = scope_of(config)
    return scope is not None and scope.is_admin is True


def operator_scope_from_env(source: str, environ: Mapping[str, str] | None = None) -> GraphScope | None:
    """Single-operator surfaces only: GraphScope.admin(source) when OPERATOR_OPT_IN_ENV is exactly "1", else None."""
    env = os.environ if environ is None else environ
    return GraphScope.admin(source) if env.get(OPERATOR_OPT_IN_ENV) == "1" else None

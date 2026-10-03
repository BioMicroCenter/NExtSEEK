"""Resolve a graph_search caller's project scope from SEEK's MySQL.

graph_search applies the same visibility rule as ``advanced_search``: a Django superuser sees every
sample, and anyone else sees the samples whose ``projects_samples`` projects intersect the projects
they belong to. Membership is read per request from MySQL, the source of truth, instead of the SEEK
REST call ``advanced_search`` makes, so no SEEK password is needed.

Membership is ``group_memberships`` joined to ``work_groups``. Former members are included, as SEEK
REST's ``Person#projects`` does today, so the scope matches ``advanced_search`` exactly.

The query builder turns a ``Scope`` into the Cypher clause; nothing else may supply scope.

``plain_scope`` hands the same scope to the assistant as plain data, for the project scope on the graph agent's
Cypher (docs/superpowers/specs/2026-09-18-graph-cypher-scope.md section 4.2): the ViewSets resolve it and pass it
down, because the engine packages never import this module.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import connections

log = logging.getLogger(__name__)

_PERSON_SQL = "SELECT person_id FROM users WHERE login = %s"

_PROJECTS_SQL = (
    "SELECT DISTINCT wg.project_id FROM group_memberships gm "
    "JOIN work_groups wg ON wg.id = gm.work_group_id "
    "WHERE gm.person_id = %s ORDER BY wg.project_id"
)

_PROJECT_TITLES_SQL = "SELECT id, title FROM projects WHERE id IN ({marks}) ORDER BY title"


@dataclass(frozen=True)
class Scope:
    """Who is asking: an unscoped admin, or a person limited to ``project_ids``.

    For a non-admin, an empty ``project_ids`` means "sees nothing", never "unscoped".
    """

    is_admin: bool
    person_id: Optional[int]
    project_ids: tuple[int, ...]


class ScopeUnavailable(Exception):
    """The caller maps to no SEEK person."""


def resolve_scope(user) -> Scope:
    """Return the caller's scope.

    A superuser gets ``Scope(True, None, ())`` without touching the database. ``is_staff`` is not
    an admin signal: the SEEK login sets it on every user.

    Raises ``ScopeUnavailable`` when the username matches no SEEK login or the login has no person.
    """
    if getattr(user, "is_superuser", False) is True:
        return Scope(is_admin=True, person_id=None, project_ids=())

    login = getattr(user, "username", None)
    if not login:
        raise ScopeUnavailable("Cannot determine project scope for this caller")

    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(_PERSON_SQL, [login])
        row = cursor.fetchone()
        if row is None or row[0] is None:
            raise ScopeUnavailable("Cannot determine project scope for this caller")
        person_id = int(row[0])

        cursor.execute(_PROJECTS_SQL, [person_id])
        project_rows = cursor.fetchall()

    project_ids = tuple(sorted({int(r[0]) for r in project_rows if r[0] is not None}))
    return Scope(is_admin=False, person_id=person_id, project_ids=project_ids)


def plain_scope(user) -> Optional[dict]:
    """The caller's scope as the assistant takes it: ``{"is_admin": bool, "project_ids": [int, ...]}``.

    ``None`` when the caller cannot be resolved (``ScopeUnavailable``) or membership cannot be read (any database
    error), logged. ``None`` refuses every graph query for the request; it never widens to "unscoped".
    """
    try:
        scope = resolve_scope(user)
    except ScopeUnavailable:
        log.warning("graph scope: the caller maps to no SEEK person; graph queries are refused for this request")
        return None
    except Exception as exc:  # noqa: BLE001 (fail closed on any membership read failure)
        log.warning("graph scope: project membership could not be read (%s); graph queries are refused for this "
                    "request", type(exc).__name__)
        return None
    return {"is_admin": bool(scope.is_admin), "project_ids": [] if scope.is_admin else list(scope.project_ids)}


def caller_block(user) -> Optional[dict]:
    """The signed-in user's own session as plain data, for the system agent's CALLER block (round 4, U5.1).

    ``{"username", "is_admin", "projects": [{"id", "name"}, ...]}``, built only from ``user`` (the request's
    authenticated account), never from the question. Memberships are read from MySQL for an admin as well as
    for anyone else (``resolve_scope`` skips the read for an admin because an admin is unscoped; here the
    question is who the user belongs to). ``projects`` is ``None`` when the read failed: the block then says so
    rather than claiming the user has no projects. ``None`` when the account has no username.
    """
    login = getattr(user, "username", None)
    if not login:
        return None
    block = {"username": str(login), "is_admin": getattr(user, "is_superuser", False) is True, "projects": None}
    try:
        with connections[settings.SEEK_DATABASE].cursor() as cursor:
            cursor.execute(_PERSON_SQL, [login])
            row = cursor.fetchone()
            if row is None or row[0] is None:
                block["projects"] = []
                return block
            cursor.execute(_PROJECTS_SQL, [int(row[0])])
            ids = sorted({int(r[0]) for r in cursor.fetchall() if r[0] is not None})
            titles = {}
            if ids:
                cursor.execute(_PROJECT_TITLES_SQL.format(marks=", ".join(["%s"] * len(ids))), ids)
                titles = {int(r[0]): r[1] for r in cursor.fetchall()}
    except Exception as exc:  # noqa: BLE001 (the block degrades to "unknown", never to a wrong list)
        log.warning("caller block: project membership could not be read (%s)", type(exc).__name__)
        return block
    block["projects"] = [{"id": i, "name": titles.get(i) or f"project {i}"} for i in ids]
    return block

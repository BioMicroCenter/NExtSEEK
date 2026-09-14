"""Resolve a graph_search caller's project scope from SEEK's MySQL.

graph_search applies the same visibility rule as ``advanced_search``: a Django superuser sees every
sample, and anyone else sees the samples whose ``projects_samples`` projects intersect the projects
they belong to. Membership is read per request from MySQL, the source of truth, instead of the SEEK
REST call ``advanced_search`` makes, so no SEEK password is needed.

Membership is ``group_memberships`` joined to ``work_groups``. Former members are included, as SEEK
REST's ``Person#projects`` does today, so the scope matches ``advanced_search`` exactly.

The query builder turns a ``Scope`` into the Cypher clause; nothing else may supply scope.
"""

from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import connections

_PERSON_SQL = "SELECT person_id FROM users WHERE login = %s"

_PROJECTS_SQL = (
    "SELECT DISTINCT wg.project_id FROM group_memberships gm "
    "JOIN work_groups wg ON wg.id = gm.work_group_id "
    "WHERE gm.person_id = %s ORDER BY wg.project_id"
)


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

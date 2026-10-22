"""SEEK edit right for batch-upload updates.

Mirrors Seek::Permissions::Authorization.is_authorized? (SEEK 1.15.1, lib/seek/permissions/authorization.rb):
edit is allowed when ANY of authorized_as_creator?, authorized_by_policy?, authorized_by_permission? holds.
  * contributor: samples.contributor_id is the caller's person.
  * policy: policies.access_type >= 3 (Policy::EDITING), whoever the caller is.
  * permission: Permission.sort_for orders a policy's permissions by contributor type
    (Person, FavouriteGroup, WorkGroup, Project, Programme, Institution) and `detect` takes the first one
    that controls the caller, so the most specific kind decides, not the highest access.
"""
from __future__ import annotations

from typing import Iterable, Sequence, Set

from sqlalchemy import text
from sqlalchemy.engine import Connection

EDITING, MANAGING = 3, 4  # access_type_allows_action?('edit') is 3..4; 5 (publishing) does not edit

YES, NO, UNKNOWN = "yes", "no", "unknown"

# Kinds whose membership this code cannot read. FavouriteGroup and WorkGroup outrank Project in SEEK, so their
# presence means the Project row may not be the one that decides; the others only grant when nothing above applies.
_UNEVALUATED_HIGHER = {"FavouriteGroup", "WorkGroup"}
_UNEVALUATED = _UNEVALUATED_HIGHER | {"Programme", "Institution"}


def _edits(access) -> bool:
    return EDITING <= (access or 0) <= MANAGING


def samples_editable_by(
    sample_ids: Iterable[int], person_id: int, project_ids: Sequence[int], conn: Connection
) -> Set[int]:
    """The subset of ``sample_ids`` the person may edit in SEEK; anything not provably editable is left out.

    ponytail: SEEK also lets WorkGroup / Institution / Programme / FavouriteGroup permissions, sample creators
    (assets_creators) and the project asset housekeeper role edit (gatekeepers only publish); those are not
    evaluated, so a sample editable only that way is refused (fails closed). Add them when a real upload needs
    it; a superuser skips this check.
    """
    return {sid for sid, v in edit_verdicts(sample_ids, person_id, project_ids, conn).items() if v == YES}


def edit_verdicts(
    sample_ids: Iterable[int], person_id: int, project_ids: Sequence[int], conn: Connection
) -> dict:
    """{sample_id: YES | NO | UNKNOWN}. NO is SEEK's own answer for the rules evaluated here; UNKNOWN is a refusal
    because a group-type permission is present that this code cannot read."""
    ids = list(dict.fromkeys(int(i) for i in sample_ids))
    projects = {int(p) for p in project_ids}
    verdicts: dict = {}
    for start in range(0, len(ids), 1000):
        chunk = ids[start : start + 1000]
        params = {f"s{i}": sid for i, sid in enumerate(chunk)}
        placeholders = ", ".join(f":s{i}" for i in range(len(chunk)))
        rows = conn.execute(
            text(
                "SELECT s.id, s.contributor_id, p.access_type, pm.contributor_type, pm.contributor_id, pm.access_type "
                "FROM samples s LEFT JOIN policies p ON p.id = s.policy_id "
                "LEFT JOIN permissions pm ON pm.policy_id = s.policy_id "
                f"WHERE s.id IN ({placeholders})"
            ),
            params,
        ).fetchall()
        by_sample: dict = {}
        for sid, contributor, general, kind, who, access in rows:
            entry = by_sample.setdefault(sid, [contributor, general, []])
            if kind is not None:
                entry[2].append((kind, who, access or 0))
        for sid, (contributor, general, perms) in by_sample.items():
            verdicts[sid] = _verdict(person_id, projects, contributor, general, perms)
    return verdicts


def _verdict(person_id, projects, contributor, general, perms) -> str:
    if contributor is not None and int(contributor) == person_id:
        return YES
    if _edits(general):
        return YES
    own = [a for k, who, a in perms if k == "Person" and who == person_id]
    if own:
        return YES if _edits(max(own)) else NO
    if any(k in _UNEVALUATED_HIGHER for k, _, _ in perms):
        return UNKNOWN
    via_project = [a for k, who, a in perms if k == "Project" and who in projects]
    if via_project and _edits(max(via_project)):
        return YES
    return UNKNOWN if any(k in _UNEVALUATED for k, _, _ in perms) else NO

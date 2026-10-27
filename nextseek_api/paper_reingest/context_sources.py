"""The reads stage 2 needs, as NessieAI.ns.paper.context.ContextSeams (spec §4, stage 2).

Every seam is strict: a database that does not answer raises ContextSourceDown, which the task retries and, past
its budget, fails the run with. It never reads as "no projects" or "no samples", which would start a paper in the
wrong project or mint duplicates.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import wraps

from django.conf import settings
from django.db import connections

from NessieAI.ns.paper.context import ContextSeams, ExportRow, ProjectFacts
from NessieAI.ns.paper.curation_models import AttributeDef, PrecedentStudy, SampleTypeDef
from NessieAI.ns.paper.rulebook import rulebook_version
from nextseek_api.services.context_catalog import load_project_context, load_sample_types_strict
from nextseek_api.services.project_export import pull_project_samples
from nextseek_api.services.reingest_lookups import attributes_for_strict


class ContextSourceDown(RuntimeError):
    """A database stage 2 reads did not answer."""


def _seek(sql: str, params=()) -> list[tuple]:
    with connections[settings.SEEK_DATABASE].cursor() as cur:
        cur.execute(sql, list(params))
        return list(cur.fetchall())


def _holes(n: int) -> str:
    return ", ".join(["%s"] * n)


def sample_type_defs_strict() -> list[SampleTypeDef]:
    return [SampleTypeDef(code=e.code, name=e.name, clade=e.clade, parents=list(e.parent_types),
                          attributes=[AttributeDef(title=a["title"], required=bool(a.get("required")))
                                      for a in attributes_for_strict(e.code)])
            for e in load_sample_types_strict()]


def house_assay_titles_strict() -> list[str]:
    with connections["default"].cursor() as cur:
        cur.execute("SELECT internal_assay_title FROM internal_assays")
        return sorted({str(row[0]) for row in cur.fetchall() if row[0]})


def project_facts_strict() -> list[ProjectFacts]:
    members: dict[int, list[tuple[str, str]]] = {}
    institutions: dict[int, set[str]] = {}
    for project_id, first, last, institution in _seek(
            "SELECT wg.project_id, p.first_name, p.last_name, i.title FROM group_memberships gm "
            "JOIN work_groups wg ON wg.id = gm.work_group_id JOIN people p ON p.id = gm.person_id "
            "LEFT JOIN institutions i ON i.id = wg.institution_id "
            "WHERE wg.project_id IS NOT NULL AND COALESCE(gm.has_left, 0) = 0"):
        members.setdefault(int(project_id), []).append((str(first or ""), str(last or "")))
        if institution:
            institutions.setdefault(int(project_id), set()).add(str(institution))
    out = []
    for project_id, title in _seek("SELECT id, title FROM projects ORDER BY id"):
        curated = load_project_context(int(project_id)) or {}
        out.append(ProjectFacts(project_id=int(project_id), title=str(title or ""),
                                alternative_names=tuple(curated.get("alternative_names") or ()),
                                pi_text=str(curated.get("pi") or ""),
                                members=tuple(members.get(int(project_id), ())),
                                institutions=tuple(sorted(institutions.get(int(project_id), ())))))
    return out


def export_rows_strict(project_ids: list[int]) -> list[ExportRow]:
    rows: dict[str, ExportRow] = {}
    for project_id in project_ids:
        for r in pull_project_samples(int(project_id)):
            try:
                meta = json.loads(r["json_metadata"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            rows.setdefault(str(r["uuid"]), ExportRow(uid=str(r["uuid"]), sample_type=str(r["sample_type"] or ""),
                                                      metadata=meta if isinstance(meta, dict) else {}))
    return list(rows.values())


def precedent_strict(project_id: int) -> list[PrecedentStudy]:
    investigations = [int(r[0]) for r in _seek(
        "SELECT investigation_id FROM investigations_projects WHERE project_id = %s", [int(project_id)])]
    if not investigations:
        return []
    studies = _seek(f"SELECT id, title FROM studies WHERE investigation_id IN ({_holes(len(investigations))}) "
                    "ORDER BY id", investigations)
    if not studies:
        return []
    assays: dict[int, list[str]] = {}
    ids = [int(s[0]) for s in studies]
    for study_id, title in _seek(f"SELECT study_id, title FROM assays WHERE study_id IN ({_holes(len(ids))}) "
                                 "ORDER BY id", ids):
        assays.setdefault(int(study_id), []).append(str(title or ""))
    return [PrecedentStudy(study_id=int(i), title=str(t or ""), assays=assays.get(int(i), [])) for i, t in studies]


def _strict(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ContextSourceDown:
            raise
        except Exception as exc:  # a database or connection error: never read as "nothing there"
            raise ContextSourceDown(f"{fn.__name__}: {type(exc).__name__}") from exc
    return call


def context_seams() -> ContextSeams:
    # Looked up by name at call time, so a test that replaces one of the functions above is seen here.
    import sys

    me = sys.modules[__name__]
    return ContextSeams(
        projects=_strict(lambda: me.project_facts_strict()), sample_types=_strict(lambda: me.sample_type_defs_strict()),
        house_assays=_strict(lambda: me.house_assay_titles_strict()),
        export_rows=_strict(lambda ids: me.export_rows_strict(ids)),
        precedent=_strict(lambda pid: me.precedent_strict(pid)),
        now=lambda: datetime.now(timezone.utc), rulebook_sha256=rulebook_version())

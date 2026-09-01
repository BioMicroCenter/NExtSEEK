"""Resolve submitted rows to (sample_id, assay_id, project_id), or to an error.

Every gate is a batch set query over the whole submission. Discovering a
problem at row 1221 of a 2000-row batch is what made the legacy path
dangerous; all of this is cheap before the first insert.

THE UID GATE IS THE EXPENSIVE LESSON. `__retrieveSampleByUID`
(seek/dbtable_sample.py) returns a record only when ``len(records) == 1``, so a
uid matching TWO rows resolves to None indistinguishably from a uid matching
zero, and that None reaches ``if sample_id>0:`` at :1564 and raises TypeError,
500ing the entire batch with every earlier row already committed. That killed
chunk 06 on 2026-08-27 after 1,220 rows.

The preflight that missed it asked "does this uid EXIST" with a join. The code
asks "does exactly ONE row have it". Those agree everywhere except duplicates,
which is the only case that can hurt. Production carries duplicate-uuid samples
and `samples.uuid` has no unique constraint, so this is standing, not
transient. The query below therefore counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from django.conf import settings
from sqlalchemy import text

from .schemas import RegistrationRow, RowError

#: Keeps IN clauses small enough to stay index-friendly, matching the chunk
#: size batch_upload/associations.py uses for the same tables.
CHUNK = 1000


def _seek_db() -> str:
    return settings.DATABASES[settings.SEEK_DATABASE]["NAME"]


def _nextseek_db() -> str:
    return settings.DATABASES[settings.NEXTSEEK_DATABASE]["NAME"]


def _in_chunks(values: Sequence[Any], size: int = CHUNK):
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _placeholders(prefix: str, chunk: Sequence[Any]) -> Tuple[str, Dict[str, Any]]:
    names = [f"{prefix}_{i}" for i in range(len(chunk))]
    return ", ".join(f":{n}" for n in names), dict(zip(names, chunk))


@dataclass(frozen=True)
class ResolvedRow:
    index: int
    sample_uid: str
    sample_id: Optional[int] = None
    assay_id: Optional[int] = None
    assay_title: Optional[str] = None
    project_id: Optional[int] = None
    error: Optional[RowError] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def resolve_sample_uids(uids: List[str], conn) -> Dict[str, int]:
    """uid -> number of `samples` rows carrying it.

    COUNT, not EXISTS. See the module docstring.
    """
    counts: Dict[str, int] = {}
    if not uids:
        return counts
    db = _seek_db()
    for chunk in _in_chunks(uids):
        holes, params = _placeholders("u", chunk)
        sql = text(
            f"SELECT uuid, COUNT(*) FROM {db}.samples "
            f"WHERE uuid IN ({holes}) GROUP BY uuid"
        )
        for uuid_value, count in conn.execute(sql, params).fetchall():
            counts[str(uuid_value)] = int(count)
    return counts


def sample_ids_for_uids(uids: List[str], conn) -> Dict[str, int]:
    """uid -> sample_id. Callers must have already proved uniqueness."""
    out: Dict[str, int] = {}
    if not uids:
        return out
    db = _seek_db()
    for chunk in _in_chunks(uids):
        holes, params = _placeholders("u", chunk)
        sql = text(f"SELECT uuid, id FROM {db}.samples WHERE uuid IN ({holes})")
        for uuid_value, sample_id in conn.execute(sql, params).fetchall():
            out[str(uuid_value)] = int(sample_id)
    return out


def projects_for_samples(sample_ids: List[int], conn) -> Dict[int, Set[int]]:
    """sample_id -> {project_id}. A set: the schema permits many-to-many."""
    out: Dict[int, Set[int]] = {}
    if not sample_ids:
        return out
    db = _seek_db()
    for chunk in _in_chunks(sample_ids):
        holes, params = _placeholders("s", chunk)
        sql = text(
            f"SELECT sample_id, project_id FROM {db}.projects_samples "
            f"WHERE sample_id IN ({holes})"
        )
        for sample_id, project_id in conn.execute(sql, params).fetchall():
            out.setdefault(int(sample_id), set()).add(int(project_id))
    return out


def projects_for_assays(assay_ids: List[int], conn) -> Dict[int, Set[int]]:
    """assay_id -> {project_id}.

    assays -> studies -> investigations_projects. Note the table name:
    `investigations_projects`, NOT `projects_investigations`, and note that
    `investigations` carries no project_id column in this SEEK schema. The
    Investigation.project_id read by services/sampletype_connections.py is a
    Neo4j node property, not this column.
    """
    out: Dict[int, Set[int]] = {}
    if not assay_ids:
        return out
    db = _seek_db()
    for chunk in _in_chunks(assay_ids):
        holes, params = _placeholders("a", chunk)
        sql = text(
            f"SELECT a.id, ip.project_id "
            f"FROM {db}.assays a "
            f"JOIN {db}.studies st ON st.id = a.study_id "
            f"JOIN {db}.investigations_projects ip "
            f"  ON ip.investigation_id = st.investigation_id "
            f"WHERE a.id IN ({holes})"
        )
        for assay_id, project_id in conn.execute(sql, params).fetchall():
            out.setdefault(int(assay_id), set()).add(int(project_id))
    return out


def assays_for_titles(titles: List[str], conn) -> Dict[str, List[Tuple[int, int, str]]]:
    """lowercased internal assay title -> [(assay_id, project_id, assay_title)].

    internal_assays -> assays_internal_assays -> assays -> studies
                    -> investigations_projects
    """
    out: Dict[str, List[Tuple[int, int, str]]] = {}
    if not titles:
        return out
    seek, ns = _seek_db(), _nextseek_db()
    for chunk in _in_chunks(titles):
        holes, params = _placeholders("t", chunk)
        sql = text(
            f"SELECT LOWER(TRIM(ia.internal_assay_title)), a.id, ip.project_id, a.title "
            f"FROM {ns}.internal_assays ia "
            f"JOIN {ns}.assays_internal_assays aia ON aia.internal_assay_id = ia.id "
            f"JOIN {seek}.assays a  ON a.id  = aia.assay_id "
            f"JOIN {seek}.studies st ON st.id = a.study_id "
            f"JOIN {seek}.investigations_projects ip "
            f"  ON ip.investigation_id = st.investigation_id "
            f"WHERE LOWER(TRIM(ia.internal_assay_title)) IN ({holes})"
        )
        for title, assay_id, project_id, assay_title in conn.execute(sql, params).fetchall():
            out.setdefault(str(title), []).append(
                (int(assay_id), int(project_id), str(assay_title or ""))
            )
    return out


def _norm(title: str) -> str:
    return title.strip().lower()


def _err(code: str, message: str, identifier: Optional[str] = None) -> RowError:
    return RowError(code=code, message=message, submitted_identifier=identifier)


def resolve(rows: List[RegistrationRow], conn) -> List[ResolvedRow]:
    """Resolve every submitted row in a fixed number of batch queries."""
    uids = sorted({r.sample_uid.strip() for r in rows})
    counts = resolve_sample_uids(uids, conn)

    unique_uids = sorted(u for u, n in counts.items() if n == 1)
    uid_to_sample = sample_ids_for_uids(unique_uids, conn) if unique_uids else {}

    sample_projects = projects_for_samples(sorted(set(uid_to_sample.values())), conn)

    explicit_ids = sorted({r.assay_id for r in rows if r.assay_id is not None})
    assay_projects = projects_for_assays(explicit_ids, conn)

    titles = sorted({_norm(r.assay) for r in rows if r.assay is not None})
    title_index = assays_for_titles(titles, conn)

    resolved: List[ResolvedRow] = []
    for index, row in enumerate(rows):
        uid = row.sample_uid.strip()
        count = counts.get(uid, 0)

        if count == 0:
            resolved.append(ResolvedRow(
                index=index, sample_uid=uid,
                error=_err("sample_uid_not_found",
                           "no row in `samples` carries this uid", uid)))
            continue
        if count != 1:
            resolved.append(ResolvedRow(
                index=index, sample_uid=uid,
                error=_err("sample_uid_not_unique",
                           f"resolves to {count} rows in `samples`; expected exactly 1",
                           uid)))
            continue

        sample_id = uid_to_sample.get(uid)
        if sample_id is None:
            # The count query said this uid resolves to exactly one row, but the
            # id query did not return it. They are separate statements, so a row
            # deleted between them — or a collation-driven key mismatch — lands
            # here. Report the row instead of raising: a KeyError out of
            # resolve() would take down the whole submission, which is precisely
            # the failure class this module exists to prevent.
            resolved.append(ResolvedRow(
                index=index, sample_uid=uid,
                error=_err("sample_uid_not_found",
                           "uid counted as unique but did not resolve to a row; "
                           "it may have been deleted mid-request", uid)))
            continue

        projects = sample_projects.get(sample_id, set())
        if not projects:
            resolved.append(ResolvedRow(
                index=index, sample_uid=uid, sample_id=sample_id,
                error=_err("sample_has_no_project",
                           "sample belongs to no project, so no assay can be "
                           "project-validated against it", uid)))
            continue

        if row.assay_id is not None:
            resolved.append(_resolve_by_id(index, uid, sample_id, projects,
                                           row.assay_id, assay_projects))
        else:
            resolved.append(_resolve_by_title(index, uid, sample_id, projects,
                                              row.assay, title_index))
    return resolved


def _resolve_by_id(index, uid, sample_id, projects, assay_id, assay_projects) -> ResolvedRow:
    assay_prj = assay_projects.get(assay_id, set())
    if not assay_prj:
        return ResolvedRow(
            index=index, sample_uid=uid, sample_id=sample_id,
            error=_err("assay_not_found",
                       "no assay with this id, or it reaches no project through "
                       "studies -> investigations_projects", str(assay_id)))
    shared = projects & assay_prj
    if not shared:
        return ResolvedRow(
            index=index, sample_uid=uid, sample_id=sample_id,
            error=_err(
                "assay_project_mismatch",
                f"assay {assay_id} belongs to project(s) "
                f"{sorted(assay_prj)} but the sample belongs to "
                f"{sorted(projects)}. SEEK assay ids are per project and a "
                f"cross-project write cannot be undone by re-running.",
                str(assay_id)))
    return ResolvedRow(index=index, sample_uid=uid, sample_id=sample_id,
                       assay_id=assay_id, project_id=min(shared))


def _resolve_by_title(index, uid, sample_id, projects, title, title_index) -> ResolvedRow:
    candidates = title_index.get(_norm(title), [])
    if not candidates:
        return ResolvedRow(
            index=index, sample_uid=uid, sample_id=sample_id,
            error=_err("internal_assay_not_found",
                       "no internal assay carries this title, or it maps to no "
                       "SEEK assay", title))
    in_project = [(a, p, t) for (a, p, t) in candidates if p in projects]
    if not in_project:
        return ResolvedRow(
            index=index, sample_uid=uid, sample_id=sample_id,
            error=_err(
                "assay_not_in_sample_project",
                f"'{title}' maps to assays in project(s) "
                f"{sorted({p for _, p, _ in candidates})} but the sample "
                f"belongs to {sorted(projects)}",
                title))
    distinct = sorted({a for a, _, _ in in_project})
    if len(distinct) > 1:
        return ResolvedRow(
            index=index, sample_uid=uid, sample_id=sample_id,
            error=_err(
                "assay_ambiguous_in_project",
                f"'{title}' resolves to {len(distinct)} assays in the sample's "
                f"project: {distinct}. Retry naming one with `assay_id`.",
                title))
    # sorted(), not in_project[0]: assays_for_titles has no ORDER BY, and MySQL
    # guarantees no row order without one. When one assay reaches two or more of
    # the sample's own projects, an unsorted index makes the reported project_id
    # and assay_title vary between identical runs on identical data. assay_id is
    # stable either way, so nothing wrong gets written, but a receipt that
    # changes run to run is a bad property for an endpoint whose whole claim is
    # an honest receipt. Matches the min() convention on the numeric path.
    assay_id, project_id, assay_title = sorted(in_project)[0]
    return ResolvedRow(index=index, sample_uid=uid, sample_id=sample_id,
                       assay_id=assay_id, assay_title=assay_title,
                       project_id=project_id)

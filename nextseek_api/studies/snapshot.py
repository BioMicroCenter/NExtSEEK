"""The studies tool's reads (tool spec 5, 6): SEEK's and dmac's MySQL, the graph (read only), SEEK GETs as the
operator. Nothing here writes.

Titles come back as stored and are compared in Python. By-id reads bind at most ``IN_CHUNK`` values a statement.
SEEK's next study id comes from ``information_schema.TABLES.AUTO_INCREMENT`` after setting
``information_schema_stats_expiry`` to 0 for the session where the server has that variable (MySQL 8 caches table
statistics); a server without it answers an error, which is ignored; a NULL falls back to ``MAX(id) + 1``.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

from django.conf import settings
from django.db import DatabaseError, connections

from nextseek_api.assay_registration.resolver import resolve_sample_uids, sample_ids_for_uids
from nextseek_api.batch_upload.db_engine import get_engine
from nextseek_api.graph_sync import sources, writer
from nextseek_api.graph_sync.writer import _one, _run
from nextseek_api.studies.buckets import buckets_from_rows

IN_CHUNK = sources.IN_CHUNK
GRAPH_MAX_STUDY_ID = "MATCH (st:Study) WHERE st.id IS NOT NULL RETURN max(st.id) AS n"
# SEEK's policy in the form its JSON API writes and reads back: PolicyHelper::ACCESS_TYPE_MAP for an access type,
# contributor_type.underscore.pluralize for a permission's resource type (base_serializer.rb, convert_policy).
POLICY_ACCESS = {0: "no_access", 1: "view", 2: "download", 3: "edit", 4: "manage"}
PERMISSION_TYPES = {"Person": "people", "Project": "projects", "Institution": "institutions",
                    "WorkGroup": "work_groups", "Programme": "programmes", "FavouriteGroup": "favourite_groups"}


class StudyRow(NamedTuple):
    id: int
    investigation_id: Optional[int]
    title: str
    description: Optional[str] = None


class AssayRow(NamedTuple):
    id: int
    study_id: Optional[int]
    title: str


def _rows(alias: str, sql: str, params=None) -> list[tuple]:
    with connections[alias].cursor() as cursor:
        cursor.execute(sql, list(params or []))
        return list(cursor.fetchall())


def _chunks(ids):
    ordered = sorted({int(i) for i in ids})
    for start in range(0, len(ordered), IN_CHUNK):
        yield ordered[start:start + IN_CHUNK]


def _holes(n: int) -> str:
    return ", ".join(["%s"] * n)


def _int(value) -> Optional[int]:
    return None if value is None else int(value)


class SnapshotReader:
    def __init__(self, session, driver, db):
        self._session = session
        self._driver = driver
        self._db = db
        self._assay_map = None

    def _seek(self, sql, params=None):
        return _rows(settings.SEEK_DATABASE, sql, params)

    def _dmac(self, sql, params=None):
        return _rows(settings.NEXTSEEK_DATABASE, sql, params)

    # --- SEEK's investigations, studies and buckets ---
    def investigations(self) -> dict:
        return {int(i): sources._text(t) for i, t in self._seek("SELECT id, title FROM investigations ORDER BY id")}

    def studies(self) -> list:
        return [StudyRow(int(i), _int(inv), sources._text(t), sources._text(d))
                for i, inv, t, d in self._seek("SELECT id, investigation_id, title, description FROM studies "
                                               "ORDER BY id")]

    def buckets(self):
        return buckets_from_rows((s.id, s.investigation_id, s.title) for s in self.studies())

    def investigation_projects(self, ids) -> dict:
        out = {int(i): set() for i in ids}
        for chunk in _chunks(ids):
            for inv, project in self._seek("SELECT investigation_id, project_id FROM investigations_projects "
                                           f"WHERE investigation_id IN ({_holes(len(chunk))})", chunk):
                out.setdefault(int(inv), set()).add(int(project))
        return out

    def project_ids_present(self, ids) -> set:
        found = set()
        for chunk in _chunks(ids):
            found.update(int(r[0]) for r in self._seek(f"SELECT id FROM projects WHERE id IN ({_holes(len(chunk))})",
                                                       chunk))
        return found

    def project_investigations(self, project_id: int) -> set:
        return {int(r[0]) for r in self._seek("SELECT investigation_id FROM investigations_projects "
                                              "WHERE project_id = %s", [int(project_id)])}

    def study_policy(self, study_id: int) -> Optional[dict]:
        """The study's policy as SEEK's API would give it to one who may manage the study, read from SEEK's
        ``policies`` and ``permissions`` tables (the API hides it from anyone else, admins included). None when the
        study has no policy row, or a value of it has no API form."""
        found = self._seek("SELECT p.id, p.access_type FROM studies s JOIN policies p ON p.id = s.policy_id "
                           "WHERE s.id = %s", [int(study_id)])
        if len(found) != 1 or _int(found[0][1]) not in POLICY_ACCESS:
            return None
        policy_id, access = found[0]
        permissions = []
        for kind, contributor, level in self._seek("SELECT contributor_type, contributor_id, access_type FROM "
                                                   "permissions WHERE policy_id = %s ORDER BY created_at, id",
                                                   [int(policy_id)]):
            kind = PERMISSION_TYPES.get(sources._text(kind))
            if kind is None or contributor is None or _int(level) not in POLICY_ACCESS:
                return None
            permissions.append({"resource": {"id": str(int(contributor)), "type": kind},
                                "access": POLICY_ACCESS[int(level)]})
        return {"access": POLICY_ACCESS[int(access)], "permissions": permissions}

    def next_study_id(self) -> int:
        try:
            self._seek("SET SESSION information_schema_stats_expiry = 0")
        except DatabaseError:
            pass
        name = settings.DATABASES[settings.SEEK_DATABASE]["NAME"]
        rows = self._seek("SELECT AUTO_INCREMENT FROM information_schema.TABLES "
                          "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'studies'", [name])
        if rows and rows[0][0]:
            return int(rows[0][0])
        return int(self._seek("SELECT COALESCE(MAX(id), 0) + 1 FROM studies")[0][0])

    # --- samples ---
    def uid_counts(self, uids) -> dict:
        with get_engine().connect() as conn:
            return resolve_sample_uids(sorted(set(uids)), conn)

    def sample_ids_for_uids(self, uids) -> dict:
        with get_engine().connect() as conn:
            return sample_ids_for_uids(sorted(set(uids)), conn)

    def existing_sample_ids(self, ids) -> set:
        found = set()
        for chunk in _chunks(ids):
            found.update(int(r[0]) for r in self._seek(f"SELECT id FROM samples WHERE id IN ({_holes(len(chunk))})",
                                                       chunk))
        return found

    def sample_rows(self, ids) -> dict:
        return {r["id"]: r for r in sources.samples_by_ids(sorted({int(i) for i in ids}))}

    def uuid_index(self, tokens) -> dict:
        return sources.uuid_to_ids_for(tokens)

    def sample_projects(self, ids) -> dict:
        return {int(k): set(v) for k, v in sources.sample_projects_for(sorted({int(i) for i in ids})).items()}

    # --- assays and their members ---
    def memberships(self, sample_ids) -> dict:
        out: dict = {}
        for chunk in _chunks(sample_ids):
            for sample, assay, direction in self._seek(
                    "SELECT asset_id, assay_id, direction FROM assay_assets WHERE asset_type = %s "
                    f"AND asset_id IN ({_holes(len(chunk))}) ORDER BY id", ["Sample", *chunk]):
                out.setdefault(int(sample), {}).setdefault(int(assay), _int(direction))
        return out

    def sample_assay_rows(self, sample_ids) -> list:
        """Every Sample row of these samples, ``(assay_id, sample_id, direction)`` in ``assay_assets.id`` order,
        duplicates kept (a share's digest, read by sample, never by whole assay)."""
        out: list = []
        for chunk in _chunks(sample_ids):
            for sample, assay, direction in self._seek(
                    "SELECT asset_id, assay_id, direction FROM assay_assets WHERE asset_type = %s "
                    f"AND asset_id IN ({_holes(len(chunk))}) ORDER BY id", ["Sample", *chunk]):
                out.append((int(assay), int(sample), _int(direction)))
        return out

    def assays(self, assay_ids) -> dict:
        out = {}
        for chunk in _chunks(assay_ids):
            for i, study, title in self._seek(f"SELECT id, study_id, title FROM assays WHERE id IN "
                                              f"({_holes(len(chunk))})", chunk):
                out[int(i)] = AssayRow(int(i), _int(study), sources._text(title))
        return out

    def study_assays(self, study_ids) -> dict:
        out = {int(s): [] for s in study_ids}
        for chunk in _chunks(study_ids):
            for i, study, title in self._seek(f"SELECT id, study_id, title FROM assays WHERE study_id IN "
                                              f"({_holes(len(chunk))}) ORDER BY id", chunk):
                out.setdefault(int(study), []).append(AssayRow(int(i), int(study), sources._text(title)))
        return out

    def assay_rows(self, assay_ids) -> dict:
        out = {int(a): [] for a in assay_ids}
        for chunk in _chunks(assay_ids):
            for assay, sample, direction in self._seek(
                    "SELECT assay_id, asset_id, direction FROM assay_assets WHERE asset_type = %s "
                    f"AND assay_id IN ({_holes(len(chunk))}) ORDER BY id", ["Sample", *chunk]):
                out.setdefault(int(assay), []).append((int(sample), _int(direction)))
        return out

    def mapping_rows(self, assay_ids) -> dict:
        out = {int(a): [] for a in assay_ids}
        for chunk in _chunks(assay_ids):
            for assay, internal in self._dmac(
                    "SELECT assay_id, internal_assay_id FROM assays_internal_assays "
                    f"WHERE internal_assay_id IS NOT NULL AND assay_id IN ({_holes(len(chunk))}) "
                    "ORDER BY assay_id, internal_assay_id", chunk):
                out.setdefault(int(assay), []).append(int(internal))
        return out

    def max_assay_id(self) -> int:
        return int(self._seek("SELECT COALESCE(MAX(id), 0) FROM assays")[0][0])

    def assay_map(self) -> dict:
        if self._assay_map is None:
            self._assay_map = sources.resolved_assay_map()
        return self._assay_map

    def sops(self) -> dict:
        return sources.sops_map()

    # --- the graph, read only ---
    def graph_max_study_id(self) -> Optional[int]:
        return _one(_run(self._driver, self._db, GRAPH_MAX_STUDY_ID, read=True), "n", None)

    def stored_edges(self, ids) -> list:
        return writer.edges_incident(self._driver, self._db, sorted({int(i) for i in ids}))

    # --- SEEK, as the operator ---
    def assay_representation(self, assay_id: int) -> dict:
        return self._session.get_assay(assay_id)

    def study_representation(self, study_id: int) -> dict:
        return self._session.get_study(study_id)

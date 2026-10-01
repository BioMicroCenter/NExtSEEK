"""One link unit (tool spec 7.2) and its undo (7.7).

A unit is one target's link changes, in one MySQL transaction on batch upload's SEEK connection
(``batch_upload.db_engine.get_connection``, which commits on a clean exit and rolls back on an exception):

1. lock (``FOR UPDATE`` on MySQL) and read the unit's source assays' Sample rows, and the clones' rows;
2. check the digest the plan recorded; a difference raises ``LinkRefused`` before any write;
3. journal ``links.intent``: the digest, the planned inserts and every row to delete in full;
4. insert with ``batch_insert_assay_assets`` (WR-01) and 5. delete with ``delete_assay_links`` (WR-02);
6. read back every planned pair (the registration executor's receipt rule): every insert present, no removal pair
   left, else ``LinkRefused``; the read-back gives each inserted pair's row id;
7. write the unit's ``samples`` outbox rows inside the transaction: at most ``writer.SAMPLE_CHUNK`` ids a row (the
   studies release's rule), the first under the unit's key, which is the unit's commit marker;
8. journal ``links.prepared`` (the read-back ids; where the outbox row went). The caller commits and journals
   ``links.committed``.

The undo deletes the inserted rows that still hold their pairs and re-inserts each deleted row with its own id, unless
that id or its pair exists again; both are reported, never forced.

A share's unit (tool spec 16.2 step 4, 16.6; T36) also adds the destination project to its samples and parents:
``projects_samples`` rows through ``batch_insert_projects_samples`` (WR-01's function), in the same transaction, the
pairs that were missing journaled so the undo deletes exactly those. Its digest covers the planned samples only
(``share.share_digest``), read under the same locks.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from nextseek_api.assay_registration.planner import existing_membership_ids
from nextseek_api.batch_upload.associations import batch_insert_assay_assets, batch_insert_projects_samples
from nextseek_api.batch_upload.insert import enqueue_samples_outbox
from nextseek_api.batch_upload.update import delete_assay_links
from nextseek_api.graph_sync.writer import SAMPLE_CHUNK
from nextseek_api.studies.planner import parent_tokens, unit_digest
from nextseek_api.studies.share import share_digest

ROW_COLUMNS = ("id", "assay_id", "asset_id", "version", "created_at", "updated_at", "relationship_type_id",
               "asset_type", "direction")
CHUNK = 1000


class LinkRefused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        self.reason, self.detail = reason, detail
        super().__init__(f"{reason}: {detail}")


@dataclass
class UnitResult:
    inserted: list
    outbox_in_transaction: bool
    sample_ids: list


def unit_key(run_id: str, unit: int) -> str:
    return f"batch:studies:{run_id}:{unit}"


def undo_key(run_id: str, unit: int) -> str:
    return f"batch:studies:{run_id}:undo:{unit}"


def outbox_rows(key: str, sample_ids) -> list[tuple[str, list[int]]]:
    """The ``samples`` outbox rows for ``sample_ids``: at most ``SAMPLE_CHUNK`` ids a row, the first under ``key``
    itself (a unit's commit marker), the next ones under ``<key>:<n>``."""
    ids = sorted({int(i) for i in sample_ids})
    parts = [ids[start:start + SAMPLE_CHUNK] for start in range(0, len(ids), SAMPLE_CHUNK)]
    return [(key if n == 0 else f"{key}:{n}", part) for n, part in enumerate(parts)]


def _enqueue(conn, key: str, sample_ids) -> bool:
    """Every outbox row of ``sample_ids`` in the caller's transaction; False when any was refused (the caller then
    enqueues them all after its commit)."""
    written = [enqueue_samples_outbox(conn, row_key, part) for row_key, part in outbox_rows(key, sample_ids)]
    return all(written)


def _params(prefix: str, values) -> tuple[str, dict]:
    names = [f"{prefix}{i}" for i in range(len(values))]
    return ", ".join(f":{n}" for n in names), dict(zip(names, values))


def _chunks(values):
    values = list(values)
    for start in range(0, len(values), CHUNK):
        yield values[start:start + CHUNK]


def _jsonable(value):
    return value.isoformat(sep=" ") if isinstance(value, datetime) else value


def _source_rows(conn, assay_ids, *, lock: bool, sample_ids=None) -> list[dict]:
    """The Sample rows of ``assay_ids`` (only those of ``sample_ids`` when given), ``FOR UPDATE`` on MySQL."""
    rows: list[dict] = []
    samples = [None] if sample_ids is None else list(_chunks(sorted({int(s) for s in sample_ids})))
    for chunk in _chunks(sorted({int(a) for a in assay_ids})):
        for sample_chunk in samples:
            holes, params = _params("a", chunk)
            where = f"asset_type = 'Sample' AND assay_id IN ({holes})"
            if sample_chunk is not None:
                sample_holes, sample_params = _params("s", sample_chunk)
                where += f" AND asset_id IN ({sample_holes})"
                params.update(sample_params)
            sql = ("SELECT id, assay_id, asset_id, version, created_at, updated_at, relationship_type_id, "
                   f"asset_type, direction FROM assay_assets WHERE {where} ORDER BY id")
            if lock and conn.dialect.name == "mysql":
                sql += " FOR UPDATE"
            found = conn.execute(text(sql), params).fetchall()
            rows.extend(dict(zip(ROW_COLUMNS, (_jsonable(v) for v in r))) for r in found)
    return rows


def project_pairs(conn, pairs, *, lock: bool = False) -> set:
    """The ``(project_id, sample_id)`` pairs of ``pairs`` that ``projects_samples`` holds (``FOR UPDATE`` on MySQL)."""
    by_project: dict = defaultdict(set)
    for project_id, sample_id in pairs:
        by_project[int(project_id)].add(int(sample_id))
    found: set = set()
    for project_id, ids in sorted(by_project.items()):
        for chunk in _chunks(sorted(ids)):
            holes, params = _params("s", chunk)
            params["p"] = project_id
            sql = f"SELECT project_id, sample_id FROM projects_samples WHERE project_id = :p AND sample_id IN ({holes})"
            if lock and conn.dialect.name == "mysql":
                sql += " FOR UPDATE"
            found.update((int(a), int(b)) for a, b in conn.execute(text(sql), params).fetchall())
    return found


def share_digest_now(conn, unit, project_id: int, *, lock: bool = False) -> str:
    """A share unit's digest read now (tool spec 16.6): its planned samples' rows in its assays and their rows for the
    destination project, locked on MySQL."""
    rows = _source_rows(conn, unit.source_assay_ids, lock=lock, sample_ids=unit.sync_ids)
    held = project_pairs(conn, [(project_id, s) for s in unit.sync_ids], lock=lock)
    return share_digest([(r["assay_id"], r["asset_id"], r["direction"]) for r in rows], held)


def _tokens(conn, sample_ids) -> dict:
    found: dict = {}
    for chunk in _chunks(sorted(sample_ids)):
        holes, params = _params("s", chunk)
        for sid, raw in conn.execute(text(f"SELECT id, json_metadata FROM samples WHERE id IN ({holes})"),
                                     params).fetchall():
            found[int(sid)] = parent_tokens(raw)
    return found


def current_digest(conn, assay_ids, *, lock: bool = False) -> tuple[str, list[dict]]:
    rows = _source_rows(conn, assay_ids, lock=lock)
    samples = {int(r["asset_id"]) for r in rows}
    tokens = _tokens(conn, samples) if samples else {}
    digest = unit_digest([(r["assay_id"], r["asset_id"], r["direction"]) for r in rows],
                         {s: tokens.get(s, []) for s in samples})
    return digest, rows


def rows_by_id(conn, ids) -> dict:
    found: dict = {}
    for chunk in _chunks(sorted({int(i) for i in ids})):
        holes, params = _params("i", chunk)
        for row_id, assay_id, asset_id in conn.execute(
                text(f"SELECT id, assay_id, asset_id FROM assay_assets WHERE id IN ({holes})"), params).fetchall():
            found[int(row_id)] = (int(assay_id), int(asset_id))
    return found


def resolved_inserts(unit, clone_ids: dict) -> list[tuple[int, int, int]]:
    return [(int(clone_ids[(x.target_key, x.source_assay_id)]), x.sample_id, x.direction) for x in unit.inserts]


def run_link_unit(conn, unit, journal, clone_ids: dict, *, run_id: str,
                  share_project_id: int | None = None) -> UnitResult:
    """One unit in the caller's transaction (the module docstring); ``share_project_id`` makes it a share's unit."""
    inserts = resolved_inserts(unit, clone_ids)
    clones = sorted({a for a, _s, _d in inserts})
    if share_project_id is None:
        digest, rows = current_digest(conn, unit.source_assay_ids, lock=True)
    else:
        digest, rows = share_digest_now(conn, unit, share_project_id, lock=True), []
    held = _source_rows(conn, clones, lock=True) if clones else []
    if digest != unit.digest:
        raise LinkRefused("digest_mismatch", f"unit {unit.unit}: its source assays changed since the plan")
    taken = {(r["assay_id"], r["asset_id"]) for r in held} & {(a, s) for a, s, _d in inserts}
    if share_project_id is not None and taken:   # a share's clone adopted at apply already holds a planned link
        raise LinkRefused("clone_changed", f"unit {unit.unit}: {len(taken)} planned link(s) already in a destination "
                                           "assay")
    removal_pairs = sorted({(r.assay_id, r.sample_id) for r in unit.removals})
    doomed = [r for r in rows if (r["assay_id"], r["asset_id"]) in set(removal_pairs)]
    planned_projects = sorted({(x.project_id, x.sample_id) for x in unit.project_inserts})
    missing_projects = sorted(set(planned_projects) - project_pairs(conn, planned_projects)) if planned_projects else []
    journal.append("links", "intent", unit=unit.unit, digest=digest, inserts=[list(i) for i in inserts],
                   deleted_rows=doomed, project_inserts=[list(p) for p in planned_projects])
    batch_insert_assay_assets([(a, s, "Sample", d, None, 1) for a, s, d in inserts], conn)
    delete_assay_links(removal_pairs, conn)
    by_project: dict = defaultdict(list)
    for project_id, sample_id in missing_projects:
        by_project[project_id].append(sample_id)
    for project_id, ids in sorted(by_project.items()):
        batch_insert_projects_samples(project_id, ids, conn)
    wanted = sorted({(a, s) for a, s, _d in inserts})
    present = existing_membership_ids(wanted, conn)
    missing = [pair for pair in wanted if pair not in present]
    if missing:
        raise LinkRefused("readback_missing", f"unit {unit.unit}: {len(missing)} inserted pair(s) not read back")
    left = existing_membership_ids(removal_pairs, conn)
    if left:
        raise LinkRefused("readback_removal_left", f"unit {unit.unit}: {len(left)} removed pair(s) still present")
    absent = set(planned_projects) - project_pairs(conn, planned_projects) if planned_projects else set()
    if absent:
        raise LinkRefused("readback_missing", f"unit {unit.unit}: {len(absent)} project pair(s) not read back")
    in_tx = _enqueue(conn, unit_key(run_id, unit.unit), unit.sync_ids)
    inserted = [[present[pair], pair[0], pair[1]] for pair in wanted]
    journal.append("links", "prepared", unit=unit.unit, inserted=inserted,
                   project_pairs_inserted=[list(p) for p in missing_projects],
                   outbox="in_transaction" if in_tx else "after_commit")
    return UnitResult(inserted=inserted, outbox_in_transaction=in_tx, sample_ids=sorted(set(unit.sync_ids)))


def undo_link_unit(conn, unit_number: int, unit_state: dict, journal, *, run_id: str) -> dict:
    inserted = (unit_state.get("prepared") or {}).get("inserted") or []
    deleted = (unit_state.get("intent") or {}).get("deleted_rows") or []
    journaled_projects = sorted({(int(p), int(s)) for p, s in
                                 (unit_state.get("prepared") or {}).get("project_pairs_inserted") or []})
    still = project_pairs(conn, journaled_projects) if journaled_projects else set()
    by_project: dict = defaultdict(list)
    for project_id, sample_id in sorted(still):
        by_project[project_id].append(sample_id)
    for project_id, ids in sorted(by_project.items()):
        for chunk in _chunks(ids):
            holes, params = _params("s", chunk)
            params["p"] = project_id
            conn.execute(text(f"DELETE FROM projects_samples WHERE project_id = :p AND sample_id IN ({holes})"),
                         params)
    held = rows_by_id(conn, [i for i, _a, _s in inserted])
    to_delete = [i for i, a, s in inserted if held.get(i) == (a, s)]
    changed = [i for i, a, s in inserted if held.get(i) != (a, s)]
    for chunk in _chunks(to_delete):
        holes, params = _params("d", chunk)
        conn.execute(text(f"DELETE FROM assay_assets WHERE id IN ({holes})"), params)
    ids_now = rows_by_id(conn, [r["id"] for r in deleted])
    pairs_now = existing_membership_ids(sorted({(r["assay_id"], r["asset_id"]) for r in deleted}), conn)
    reinsert = [r for r in deleted if r["id"] not in ids_now and (r["assay_id"], r["asset_id"]) not in pairs_now]
    skipped = [r["id"] for r in deleted if r not in reinsert]
    for row in reinsert:
        conn.execute(text("INSERT INTO assay_assets (id, assay_id, asset_id, version, created_at, updated_at, "
                          "relationship_type_id, asset_type, direction) VALUES (:id, :assay_id, :asset_id, :version, "
                          ":created_at, :updated_at, :relationship_type_id, :asset_type, :direction)"),
                     {k: row.get(k) for k in ROW_COLUMNS})
    sample_ids = sorted({s for _i, _a, s in inserted} | {int(r["asset_id"]) for r in deleted}
                        | {s for _p, s in journaled_projects})
    in_tx = _enqueue(conn, undo_key(run_id, unit_number), sample_ids)
    return {"deleted": len(to_delete), "not_deleted_changed": changed, "reinserted": len(reinsert),
            "not_reinserted": skipped, "project_pairs_deleted": len(still),
            "project_pairs_gone": [list(p) for p in sorted(set(journaled_projects) - still)],
            "sample_ids": sample_ids, "outbox_in_transaction": in_tx}

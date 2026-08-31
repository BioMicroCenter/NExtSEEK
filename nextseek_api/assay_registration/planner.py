"""Turn a submitted batch into a plan: what writes, what already exists, what is skipped.

Nothing here touches the database beyond reads. The plan is complete before the
executor opens its transaction, which is the whole point: the legacy path
discovered problems at row 1221 of 2000 with 1,220 rows already committed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from sqlalchemy import text

from .resolver import ResolvedRow, _in_chunks, _seek_db, resolve
from .schemas import RegistrationRow


def existing_membership_ids(pairs: List[Tuple[int, int]], conn) -> Dict[Tuple[int, int], int]:
    """(assay_id, sample_id) -> existing assay_assets.id, for pairs already present.

    Grouped by assay_id to keep the IN clause on the indexed column, matching
    batch_upload/associations.py's own pre-SELECT.
    """
    out: Dict[Tuple[int, int], int] = {}
    if not pairs:
        return out
    db = _seek_db()
    by_assay: Dict[int, List[int]] = {}
    for assay_id, sample_id in pairs:
        by_assay.setdefault(int(assay_id), []).append(int(sample_id))

    for assay_id, sample_ids in by_assay.items():
        for chunk in _in_chunks(sorted(set(sample_ids))):
            names = [f"s_{i}" for i in range(len(chunk))]
            params: Dict[str, Any] = dict(zip(names, chunk))
            params["aid"] = assay_id
            holes = ", ".join(f":{n}" for n in names)
            sql = text(
                f"SELECT assay_id, asset_id, id FROM {db}.assay_assets "
                f"WHERE assay_id = :aid AND asset_type = 'Sample' "
                f"AND asset_id IN ({holes})"
            )
            for a_id, s_id, row_id in conn.execute(sql, params).fetchall():
                out[(int(a_id), int(s_id))] = int(row_id)
    return out


@dataclass
class Plan:
    resolved: List[ResolvedRow]
    to_write: List[ResolvedRow]
    #: row index -> the existing assay_assets.id, so the response can carry a
    #: real primary key for already_present rows too.
    already_present: Dict[int, int]
    skipped: List[ResolvedRow]
    total_rows: int

    def execution_mode(self, threshold: int) -> str:
        return "asynchronous" if self.total_rows > threshold else "synchronous"


def plan_batch(rows: List[RegistrationRow], conn) -> Plan:
    resolved = resolve(rows, conn)
    good = [r for r in resolved if r.ok]
    skipped = [r for r in resolved if not r.ok]

    pairs = sorted({(r.assay_id, r.sample_id) for r in good})
    present = existing_membership_ids(pairs, conn)

    already: Dict[int, int] = {}
    to_write: List[ResolvedRow] = []
    seen: set = set()
    for row in good:
        key = (row.assay_id, row.sample_id)
        if key in present:
            already[row.index] = present[key]
            continue
        if key in seen:
            # The same pair submitted twice is one insert. The duplicate row
            # is resolved by the executor's read-back like any other.
            continue
        seen.add(key)
        to_write.append(row)

    return Plan(resolved=resolved, to_write=to_write, already_present=already,
                skipped=skipped, total_rows=len(rows))

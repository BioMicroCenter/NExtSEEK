"""The assay list Nessie chooses from, built from the box's own ``dmac.internal_assays``.

The committed ``min_assays_db.json`` ships to every box, so a box missing some of its names would still list them.
Here the titles come from the box's table, cached like the graph vocabulary. A title that is also in the file keeps
the file's Description and Tags; a title only the box has gets empty ones (the table carries titles only).
When the table cannot be read the committed file is served unchanged.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

SQL = "SELECT internal_assay_title FROM dmac.internal_assays ORDER BY internal_assay_title"
TTL_S = 3600.0
FAILURE_MEMORY_S = 60.0

_lock = threading.Lock()
#: id(config) -> (read at, ttl, list or None)
_cache: dict[int, tuple[float, float, list | None]] = {}


def _titles(config) -> list[str]:
    from .config import live_db_conn  # noqa: PLC0415
    conn = live_db_conn(config, env="prod")
    if conn is None:
        raise RuntimeError("no database connection")
    cur = conn.cursor()
    try:
        cur.execute(SQL)
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass
    out = []
    for r in rows:
        t = r.get("internal_assay_title") if isinstance(r, dict) else r[0]
        if isinstance(t, (bytes, bytearray)):
            t = bytes(t).decode("utf-8", errors="replace")
        if isinstance(t, str) and t.strip():
            out.append(t.strip())
    return list(dict.fromkeys(out))


def min_assays(config, committed: list) -> list:
    """The box's assay rows (``Name``, ``Description``, ``Tags``), or ``committed`` when its table cannot be read."""
    key, now = id(config), time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < hit[1]:
            return committed if hit[2] is None else hit[2]
        try:
            titles = _titles(config)
            if not titles:
                raise RuntimeError("internal_assays is empty")
            have = {r.get("Name"): r for r in committed if isinstance(r, dict)}
            rows = [have.get(t) or {"Name": t, "Description": None, "Tags": None} for t in titles]
            _cache[key] = (now, TTL_S, rows)
            return rows
        except Exception as exc:  # the table is unreachable: serve the committed file, try again soon
            log.warning("assay list: serving the committed file (%s)", exc)
            _cache[key] = (now, FAILURE_MEMORY_S, None)
            return committed

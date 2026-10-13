"""The assay list Nessie chooses from, built from the box's own ``dmac.internal_assays``.

The catalog list (``min_assays_db.json``: the committed file, which the app rewrites from the box's
``dmac.assay_context`` at process start) is curated, and its names need not be the ones the box's table holds. Here
the titles come from the table. A title the catalog also has, compared ignoring case as MySQL's collation compares
them, keeps the catalog's Description and Tags under the table's spelling; a title only the table has gets empty ones.
When the table cannot be read, or is empty, the catalog list is served unchanged.

One read per database (host, port, user) per ``TTL_S``, made on first use, never at boot: the per-turn copies of a
config share it. A failed read is remembered ``FAILURE_MEMORY_S``, as the graph catalog remembers one. Each read
opens its own connection and closes it: the app's connections run with autocommit off, so a reused one would keep
answering from its first snapshot and keep that transaction open.
"""
from __future__ import annotations

import logging
import threading
import time

from .graph_catalog import FAILURE_MEMORY_S

log = logging.getLogger(__name__)

SQL = "SELECT internal_assay_title FROM dmac.internal_assays ORDER BY internal_assay_title"
TTL_S = 3600.0

# The clock; tests patch this name.
_clock = time.monotonic
_lock = threading.Lock()
#: (host, port, user) -> (read at, ttl, rows or None)
_cache: dict[tuple[str, str, str], tuple[float, float, list | None]] = {}


def _key(config) -> tuple[str, str, str]:
    return tuple(str(getattr(config, name, "") or "") for name in ("MYSQL_HOST_PROD", "MYSQL_PORT", "MYSQL_USER"))


def _titles(config) -> list[str]:
    connect = getattr(config, "_connect_db", None)
    conn = connect(env="prod") if connect is not None else None
    if conn is None:
        raise RuntimeError("no database connection")
    try:
        cur = conn.cursor()
        cur.execute(SQL)
        rows = cur.fetchall() or []
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 (closing must never hide the read's own error)
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
    key, now = _key(config), _clock()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < hit[1]:
            return committed if hit[2] is None else hit[2]
        try:
            titles = _titles(config)
            if not titles:
                raise RuntimeError("internal_assays is empty")
            have: dict[str, dict] = {}
            for r in committed:
                if isinstance(r, dict) and isinstance(r.get("Name"), str):
                    have.setdefault(r["Name"].casefold(), r)
            rows = []
            for t in titles:
                r = have.get(t.casefold())
                rows.append({"Name": t, "Description": None, "Tags": None} if r is None
                            else r if r["Name"] == t else dict(r, Name=t))
            _cache[key] = (now, TTL_S, rows)
            return rows
        except Exception as exc:  # the table is unreachable: serve the catalog list, try again soon
            log.warning("assay list: serving the catalog list (%s)", exc)
            _cache[key] = (now, FAILURE_MEMORY_S, None)
            return committed

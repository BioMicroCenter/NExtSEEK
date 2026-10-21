"""Size guard: a chat value too big for the row goes to a file, and the row keeps a link.

MySQL refuses a statement over max_allowed_packet (64 MB on dev and prod), and one GEO turn can carry 100 MB.
``SpillJSONField`` is a JSONField that, on write, replaces every big piece of its value with
``{"$spill": "ab/<sha256>.json.gz", "bytes": N}`` and, on read, puts the pieces back, so no reader changes. The
files sit in ``settings.CHAT_SPILL_DIR`` (on the media volume both the worker and web containers mount), gzipped
and named by the SHA-256 of their JSON, so the same data saved three times is one file and a file never changes.
``sweep`` deletes the files no row links to. Design: SIZE-GUARD-DESIGN.md.
"""
from __future__ import annotations

import contextlib
import contextvars
import gzip
import hashlib
import json
import logging
import os
import re
import time

from django.conf import settings
from django.db import connection, models

logger = logging.getLogger(__name__)

SPILL_VALUE_BYTES = 256 * 1024
SPILL_COLUMN_BYTES = 4 * 1024 * 1024
#: ``chat_spill --inline`` refuses a row that would come back this big: MySQL's packet limit is 64 MB.
INLINE_MAX_BYTES = 60 * 1024 * 1024

_PATH = re.compile(r"[0-9a-f]{2}/[0-9a-f]{64}\.json\.gz")
_FIND = re.compile(r"[0-9a-f]{2}/[0-9a-f]{64}\.json\.gz")
_LINK_KEYS = {"$spill", "bytes"}

_off = contextvars.ContextVar("chat_spill_off", default=False)

#: (table, primary key column, column): every column that holds a SpillJSONField.
SPILL_COLUMNS = (
    ("assistant_chat_session", "session_id", "results_history"),
    ("assistant_chat_session", "session_id", "last_debug"),
    ("assistant_chat_session", "session_id", "extra_state"),
    ("assistant_query_task", "id", "result"),
    ("assistant_query_task", "id", "progress"),
)


@contextlib.contextmanager
def spilling_off():
    """Writes inside the block keep every value inline (``chat_spill --inline``, and tests that need old rows)."""
    token = _off.set(True)
    try:
        yield
    finally:
        _off.reset(token)


def _dumps(value, encoder=None) -> str:
    return json.dumps(value, cls=encoder)


def _root() -> str:
    return settings.CHAT_SPILL_DIR


def _write(text: str) -> dict:
    """Store one JSON text as a file (once) and return its link."""
    raw = text.encode()
    sha = hashlib.sha256(raw).hexdigest()
    rel = f"{sha[:2]}/{sha}.json.gz"
    path = os.path.join(_root(), rel)
    if os.path.exists(path):
        os.utime(path)  # a re-linked file must not look old to the sweep
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(gzip.compress(raw, mtime=0))
        os.replace(tmp, path)
    return {"$spill": rel, "bytes": len(raw)}


def spill(value, encoder=None):
    """``value`` with every big piece replaced by a link. A value within SPILL_VALUE_BYTES comes back as it is.

    Dicts are structure: a dict's values, and the items of a top-level list (bundles, progress events), are reduced
    in place and the dict stays in the row, so its small keys stay where SQL lookups (``result__bundle_id``,
    ``extra_state__icontains``) and the raw-SQL readers find them. Any other value (a string, a list below the top
    level) over SPILL_VALUE_BYTES goes to a file whole. Only a column still over SPILL_COLUMN_BYTES after that
    (hundreds of small bundles) goes to one file whole.
    """
    if _off.get():
        return value
    if len(_dumps(value, encoder)) <= SPILL_VALUE_BYTES:
        return value
    out = [_reduce(v, encoder) for v in value] if isinstance(value, list) else _reduce(value, encoder)
    text = _dumps(out, encoder)
    return _write(text) if len(text) > SPILL_COLUMN_BYTES else out


def _reduce(value, encoder):
    """A dict with its values reduced; any other value as it is, or its link when it is over the value limit."""
    if isinstance(value, dict):
        return {k: _reduce(v, encoder) for k, v in value.items()}
    text = _dumps(value, encoder)
    return _write(text) if len(text) > SPILL_VALUE_BYTES else value


def _is_link(value) -> bool:
    return (isinstance(value, dict) and value.keys() == _LINK_KEYS
            and isinstance(value["$spill"], str) and _PATH.fullmatch(value["$spill"]) is not None)


def hydrate(value):
    """``value`` with every link replaced by its file's content. A missing or unreadable file reads as None."""
    if _is_link(value):
        path = os.path.join(_root(), value["$spill"])
        try:
            with gzip.open(path, "rb") as fh:
                return hydrate(json.loads(fh.read()))
        except Exception:
            logger.error("chat spill file %s is missing or unreadable; its value reads as None", path, exc_info=True)
            return None
    if isinstance(value, dict):
        return {k: hydrate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [hydrate(v) for v in value]
    return value


class SpillJSONField(models.JSONField):
    def get_prep_value(self, value):
        return super().get_prep_value(spill(value, self.encoder))

    def from_db_value(self, value, expression, connection):
        value = super().from_db_value(value, expression, connection)
        out = hydrate(value)
        # A whole column whose file is gone (hydrate logged it) reads as the column's default, so the chat still opens.
        return self.get_default() if out is None and _is_link(value) else out


def _text(cell) -> str:
    return cell.decode() if isinstance(cell, bytes) else (cell or "")


def linked_paths() -> set[str]:
    """Every file a row links to, directly or through another file. Raw SQL and a regex: the ORM would hydrate."""
    found: set[str] = set()
    with connection.cursor() as cur:
        for table, pk, col in SPILL_COLUMNS:
            cur.execute(f"SELECT {pk} FROM {table}")
            for (key,) in cur.fetchall():
                cur.execute(f"SELECT {col} FROM {table} WHERE {pk} = %s", [key])
                found.update(_FIND.findall(_text(cur.fetchone()[0])))
    todo = list(found)
    while todo:
        try:
            with gzip.open(os.path.join(_root(), todo.pop()), "rb") as fh:
                inner = set(_FIND.findall(fh.read().decode())) - found
        except OSError:
            continue
        found |= inner
        todo.extend(inner)
    return found


def sweep(min_age_s: int = 86400, dry_run: bool = False) -> dict:
    """Delete the files no row links to that are older than ``min_age_s`` (a file is written before its row)."""
    linked = linked_paths()
    cutoff = time.time() - min_age_s
    counts = {"kept": 0, "deleted": 0, "bytes": 0}
    for folder, _, names in os.walk(_root()):
        for name in names:
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, _root())
            st = os.stat(path)
            if rel in linked or st.st_mtime > cutoff or not _PATH.fullmatch(rel):
                counts["kept"] += 1
                continue
            logger.info("chat spill sweep: %s %s (%d bytes)", "would delete" if dry_run else "deleting", rel, st.st_size)
            counts["deleted"] += 1
            counts["bytes"] += st.st_size
            if not dry_run:
                os.remove(path)
    return counts

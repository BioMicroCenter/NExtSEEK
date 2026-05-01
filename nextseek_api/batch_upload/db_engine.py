"""SQLAlchemy engine singleton with CAPABILITIES detection."""
from __future__ import annotations

import logging
import re
import threading
from contextlib import contextmanager
from typing import Dict, Generator

from sqlalchemy import create_engine as _sa_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from .config import get_sqlalchemy_url

log = logging.getLogger(__name__)

_engine: Engine | None = None
_engine_lock = threading.Lock()

# MariaDB 10.5+ supports INSERT ... RETURNING
CAPABILITIES: Dict[str, bool] = {"insert_returning": False}

_MARIADB_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)-MariaDB", re.IGNORECASE)


def _detect_capabilities(engine: Engine) -> None:
    """Detect DB capabilities from VERSION() string."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT VERSION()")).fetchone()
            if row:
                version_str = row[0]
                log.info("Database version: %s", version_str)
                m = _MARIADB_VERSION_RE.search(version_str)
                if m:
                    major, minor = int(m.group(1)), int(m.group(2))
                    if (major, minor) >= (10, 5):
                        CAPABILITIES["insert_returning"] = True
                        log.info("INSERT ... RETURNING supported (MariaDB %d.%d)", major, minor)
                    else:
                        log.info("INSERT ... RETURNING NOT supported (MariaDB %d.%d)", major, minor)
                else:
                    log.info("Non-MariaDB detected; INSERT ... RETURNING disabled")
    except Exception:
        log.exception("Failed to detect DB capabilities")


def get_engine() -> Engine:
    """Return the singleton SQLAlchemy engine, creating it on first call."""
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        url = get_sqlalchemy_url()
        _engine = _sa_create_engine(
            url,
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo=False,
        )
        _detect_capabilities(_engine)
        return _engine


@contextmanager
def get_connection() -> Generator[Connection, None, None]:
    """Provide a transactional connection context.

    Commits on clean exit, rolls back on exception.
    """
    engine = get_engine()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            yield conn
            trans.commit()
        except BaseException:
            trans.rollback()
            raise


def dispose_engine() -> None:
    """Dispose the engine and reset the singleton."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None
            log.info("SQLAlchemy engine disposed")

"""A dead MySQL connection must never be handed to a caller.

``ChatConfig._db_conn`` is opened once per gunicorn worker (config.py:104, via the
``NEXTSEEK_CHAT_CONFIG = ChatConfig()`` singleton in dmac/local_settings.py) and kept
for the life of that process. The server eventually drops it.

mysql-connector's dead connection object is still **truthy**, so the

    conn = config._db_conn or config._connect_db(env="prod")

idiom used at five call sites (config.py:654, config.py:1007, runners.py:239, :466,
:745) can never reconnect: ``or`` only falls through on ``None``. It hands the corpse
straight to ``cursor()``, which raises

    OperationalError(-1, 'MySQL Connection not available', None)

for every project report on that worker, permanently, until the process restarts.
Only config.py:858 ever resets ``_db_conn`` to ``None``, so the other four sites stay
poisoned. Observed in production 2026-08-20 with workers 2.9 days old; the reporter
replied "The reporter agent could not run the project report. Error: OperationalError(
-1, 'MySQL Connection not available', None)".

The double below is a *real* ``MySQLConnection`` that was never connected. It
reproduces the production object state exactly (truthy, ``is_connected()`` False,
``cursor()`` raising that identical error) without touching a network.
"""
from __future__ import annotations

import types

from mysql.connector.connection import MySQLConnection

from chat_nextseek.config import ChatConfig, live_db_conn
from chat_nextseek.reports.runners import run_project_sample_report


def _dead_conn() -> MySQLConnection:
    """A real mysql-connector connection in the exact state production hits."""
    return MySQLConnection()


class _FakeCursor:
    def __init__(self, rows):
        self._all = rows
        self._rows = []

    def execute(self, sql, params=None):
        key = sql.rsplit(".", 1)[-1].strip().rstrip(";")
        self._rows = self._all.get(key, self._all.get("*", []))

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _LiveConn:
    """A healthy connection: reports itself connected and serves rows."""

    def __init__(self, rows=None):
        self.rows = rows or {}

    def is_connected(self):
        return True

    def cursor(self, dictionary=False):
        return _FakeCursor(self.rows)

    def close(self):
        pass


def _cfg(existing, replacement):
    """A ChatConfig whose singleton is `existing` and whose reconnect yields `replacement`."""
    cfg = ChatConfig.__new__(ChatConfig)          # bypass __init__: it dials the DB
    cfg._db_conn = existing
    cfg._connect_db = lambda env="prod": replacement  # type: ignore[assignment]
    return cfg


# --------------------------------------------------------------------------
# The accessor
# --------------------------------------------------------------------------

def test_live_db_conn_replaces_a_dead_connection():
    dead, fresh = _dead_conn(), _LiveConn()
    assert bool(dead) is True, "precondition: the corpse is truthy, which is why `or` fails"
    cfg = _cfg(dead, fresh)

    assert cfg._live_db_conn(env="prod") is fresh


def test_live_db_conn_clears_the_dead_singleton():
    """A corpse must be evicted, or the next caller is handed it again."""
    dead, fresh = _dead_conn(), _LiveConn()
    cfg = _cfg(dead, fresh)

    cfg._live_db_conn(env="prod")

    assert cfg._db_conn is not dead


def test_live_db_conn_caches_the_replacement():
    """Store the reconnect, or every report reopens a connection the runners never close."""
    dead, fresh = _dead_conn(), _LiveConn()
    cfg = _cfg(dead, fresh)

    cfg._live_db_conn(env="prod")

    assert cfg._db_conn is fresh


def test_live_db_conn_reuses_a_healthy_connection():
    """Liveness checking must not turn every call into a reconnect."""
    healthy = _LiveConn()
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._db_conn = healthy

    def _boom(env="prod"):
        raise AssertionError("reconnected despite a healthy connection")

    cfg._connect_db = _boom  # type: ignore[assignment]

    assert cfg._live_db_conn(env="prod") is healthy


def test_live_db_conn_returns_none_when_the_database_is_unreachable():
    """A genuinely down DB degrades to None; callers already handle that."""
    cfg = _cfg(_dead_conn(), None)

    assert cfg._live_db_conn(env="prod") is None


def test_live_db_conn_works_on_a_duck_typed_config():
    """The report runners are handed lightweight config doubles, not a ChatConfig.

    Anything exposing ``_db_conn``/``_connect_db`` must be supported, or the fix
    breaks every caller that passes a stand-in instead of the real config object.
    """
    dead, fresh = _dead_conn(), _LiveConn()
    cfg = types.SimpleNamespace(_db_conn=dead, _connect_db=lambda **k: fresh)

    assert live_db_conn(cfg, env="prod") is fresh
    assert cfg._db_conn is fresh


# --------------------------------------------------------------------------
# The call sites that were poisoned
# --------------------------------------------------------------------------

def test_name_to_id_map_recovers_when_the_singleton_died():
    """config.py:1007 swallowed the OperationalError and returned {} forever."""
    fresh = _LiveConn({"projects": [(1, "Published Data")]})
    cfg = _cfg(_dead_conn(), fresh)

    assert cfg._load_name_to_id_from_db("seek_production.projects") == {"PUBLISHED DATA": 1}


def test_sample_report_recovers_when_the_singleton_died(tmp_path):
    """runners.py:239 — this is the exact path that failed in production."""
    fresh = _LiveConn({"*": [{"project_id": 1, "sample_id": 10, "uuid": "TIS-240422DFC-6"}]})
    cfg = _cfg(_dead_conn(), fresh)

    result = run_project_sample_report(cfg, project=None, outputs_root=tmp_path)

    assert result["ok"] is True, result.get("error")


def test_sample_report_reports_a_clean_error_when_the_database_is_down(tmp_path):
    """Down-but-honest must stay distinguishable from the dead-connection bug."""
    cfg = _cfg(_dead_conn(), None)

    result = run_project_sample_report(cfg, project=None, outputs_root=tmp_path)

    assert result["ok"] is False
    assert result["error"] == "DB connection failed"
    assert "MySQL Connection not available" not in str(result["error"])

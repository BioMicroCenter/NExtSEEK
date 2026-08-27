import os
from pathlib import Path

import orjson
import pytest

from nextseek_api.attributes.tests.real_boundary import (
    AttributeFaultController,
    DisposableAttributeBroker,
    DisposableAttributeDatabase,
    RailsLikeWorkload,
    SqlTelemetry,
    WorkerHandle,
)

MANIFEST = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")


def _purge_disposable_binlogs(database):
    # Bound disposable-server disk: heavy benchmark cells rotate 10-30GB of
    # binlog each, and across the 162-cell matrix that exhausts the host.
    # Runs in per-case teardown after telemetry has closed, so measured
    # statements keep their real binlog write cost; only rotated log files
    # are deleted from the disposable server.
    import MySQLdb

    connection = MySQLdb.connect(db="performance_schema", **database._connection_kwargs)
    try:
        cursor = connection.cursor()
        cursor.execute("FLUSH BINARY LOGS")
        cursor.execute("PURGE BINARY LOGS BEFORE '2038-01-19 00:00:00'")
    finally:
        connection.close()


@pytest.fixture(scope="session", autouse=True)
def _attribute_default_db_environment_sync(django_db_setup):
    """task-08 spec §7 Edit 3 harness obligation.

    `dmac.attribute_performance_settings.DATABASES["default"]` has no
    `TEST.NAME` override, so pytest-django's own `django_db_setup` (a
    built-in, session-scoped fixture -- forced to run here via the
    dependency) swaps `connections["default"].settings_dict` to a
    `"test_"`-prefixed database and runs every default-alias migration
    against *that* (same lifecycle "default" already goes through under
    `dmac.test_settings`'s `:memory:` SQLite; just a real, network-visible
    MariaDB backend now). That swap only mutates Django's live in-process
    connection state -- it never touches `os.environ`.

    Every real subprocess this suite spawns
    (`DisposableAttributeBroker.start_worker`/`restart_worker`'s Celery
    worker in `real_boundary.py`; `test_sync_recovery.py`'s
    `_spawn_web_owner` web-owner simulation) builds its child environment
    from `os.environ` (`dict(os.environ)` / `os.environ.copy()`) at spawn
    time, not from Django's live connection state. Without this sync, a
    freshly spawned subprocess re-importing
    `dmac.attribute_performance_settings` would resolve the original,
    pre-swap database name -- one pytest-django never actually created --
    instead of the one the pytest parent is really using.

    Session-scoped and autouse: runs once, before any test body, so every
    later `os.environ`-derived subprocess environment already carries the
    corrected coordinates. No-ops under any non-MariaDB `default` alias
    (e.g. `dmac.test_settings`'s SQLite `:memory:`), so it is safe for every
    other task's lane that shares this same pytest_plugin.
    """
    from django.db import connections

    default = connections["default"].settings_dict
    if default.get("ENGINE") == "django.db.backends.mysql":
        os.environ["ATTRIBUTE_DEFAULT_DATABASE_NAME"] = str(default["NAME"])
        os.environ["ATTRIBUTE_DEFAULT_DB_HOST"] = str(default["HOST"])
        os.environ["ATTRIBUTE_DEFAULT_DB_PORT"] = str(default["PORT"])
        os.environ["ATTRIBUTE_DEFAULT_DB_USER"] = str(default["USER"])
        os.environ["ATTRIBUTE_DEFAULT_DB_PASSWORD"] = str(default["PASSWORD"])
    yield


@pytest.fixture
def disposable_attribute_db():
    database = DisposableAttributeDatabase.from_environment()
    identity_path = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]) / "boundary-identity.json"
    frozen = orjson.loads(identity_path.read_bytes())
    if frozen["server_identity"] != database.server_identity or frozen["database_uuid"] != database.database_uuid:
        raise RuntimeError("fixture attached to a different disposable boundary")
    try:
        yield database
    finally:
        if os.environ.get("ATTRIBUTE_TEST_DATABASE_PRECREATED") == "1":
            database.detach_django_alias()
        else:
            database.teardown()
            database.assert_torn_down()
        _purge_disposable_binlogs(database)


@pytest.fixture
def attribute_faults():
    points = set(orjson.loads(MANIFEST.read_bytes())["fault_points"])
    controller = AttributeFaultController(points)
    yield controller
    controller.clear()


@pytest.fixture
def rails_like_workload():
    return RailsLikeWorkload()


@pytest.fixture
def sql_telemetry(disposable_attribute_db):
    telemetry = SqlTelemetry(disposable_attribute_db)
    disposable_attribute_db._telemetry = telemetry
    try:
        yield telemetry
    finally:
        disposable_attribute_db._telemetry = None
        telemetry.close()


@pytest.fixture
def attribute_broker_lane():
    broker = DisposableAttributeBroker.from_environment()
    try:
        yield broker
    finally:
        if broker._worker is not None and broker._worker.poll() is None:
            broker.kill_worker(WorkerHandle(broker._worker.pid, broker._worker_argv[7], broker.namespace))
        if broker._event_recorder is not None and broker._event_recorder.poll() is None:
            broker._event_recorder.terminate()
            broker._event_recorder.wait(timeout=30)
        broker.assert_torn_down()

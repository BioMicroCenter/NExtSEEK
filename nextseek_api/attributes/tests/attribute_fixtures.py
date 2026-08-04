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

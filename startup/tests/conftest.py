from __future__ import annotations

import pytest

from startup.steps import schema_fixups as sf

pytest_plugins = ["nextseek_api.attributes.tests.attribute_fixtures"]


@pytest.fixture(autouse=True)
def _reset_managed_schema(request):
    """`ATTRIBUTE_TEST_DATABASE_PRECREATED=1` means every test across
    `startup/tests/` that uses `disposable_attribute_db` shares one physical
    disposable database for the whole schema-lane run (`disposable_attribute_db`'s
    teardown only detaches the Django alias, it never drops/recreates the
    schema). Reset the managed-index physical state, the ownership-marker
    table, and the two seeded content tables before each test so tests are
    independent regardless of execution order or module."""
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    disposable_attribute_db = request.getfixturevalue("disposable_attribute_db")
    connection = disposable_attribute_db.connect()
    database = disposable_attribute_db.database_name
    cursor = connection.cursor()
    for index in sf.indexes_for_database(database):
        state, _ = sf.attribute_index_readiness(connection, index)
        if state == "present":
            cursor.execute(index.reverse_sql())
    if sf._table_exists_on_connection(connection, database, sf._MARKER_TABLE):
        cursor.execute(f"DROP TABLE `{sf._MARKER_TABLE}`")
    for table in ("sample_attributes", "samples"):
        if sf._table_exists_on_connection(connection, database, table):
            cursor.execute(f"DELETE FROM `{table}`")
    connection.commit()
    connection.close()
    yield

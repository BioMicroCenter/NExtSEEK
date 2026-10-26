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


@pytest.fixture(autouse=True)
def _no_docker_in_the_web_swap(request, monkeypatch):
    """`rebuild` asks docker whether the new image has pending migrations before it
    swaps web. No test may reach docker: answer "pending", which sends the rebuild
    down the stop-first branch the older tests were written against. The tests that
    are about the swap stub it themselves, and test_web_swap drives the real code."""
    if request.module.__name__.endswith("test_web_swap"):
        return
    from startup.lib import web_swap

    monkeypatch.setattr(web_swap, "migrations_pending", lambda run: (True, "(test)"))


@pytest.fixture(autouse=True)
def _no_docker_or_host_probes_in_the_rebuild_preflight(request, monkeypatch):
    """The rebuild preflight and the ownership passes shell out to docker and read host accounts. Tests that
    drive them set their own stubs; every other rebuild test gets these quiet defaults."""
    if request.module.__name__.endswith(("test_ownership", "test_validate_app_ids", "test_docker_ops")):
        return
    from startup.steps import validate, volumes

    monkeypatch.setattr(validate, "check_app_ids", lambda gid: validate.HealthResult("app uid/gid", True, "ok"))
    monkeypatch.setattr(volumes, "ensure_secret_group_read", lambda root, env: [])
    monkeypatch.setattr(volumes, "app_user_change", lambda root, env: False)
    monkeypatch.setattr(volumes, "ensure_app_ownership",
                        lambda root, env, *, check_only, skip_cc_users=False, say=None: {})

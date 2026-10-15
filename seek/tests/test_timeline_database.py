"""The timeline's MySQL pool: built once, and a connection error is raised as itself."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from mysql.connector import Error

from seek.timeline.core import database

SETTINGS = dict(
    SEEK_DATABASE="seek",
    DATABASES={
        "default": {"ENGINE": "x", "NAME": "appdb"},
        "seek": {"ENGINE": "x", "NAME": "seekdb", "USER": "u", "PASSWORD": "p", "HOST": "h"},
    },
)


@pytest.fixture(autouse=True)
def _fresh_pool():
    cache_clear = getattr(database._get_pool, "cache_clear", None) if hasattr(database, "_get_pool") else None
    if cache_clear:
        cache_clear()
    yield
    if cache_clear:
        cache_clear()


def test_db_error_is_not_masked():
    with patch.object(database, "get_db_connection", side_effect=Error("down")):
        with pytest.raises(Error):
            database.execute_query("SELECT 1", ())


@override_settings(**SETTINGS)
def test_pool_is_built_once():
    with patch("mysql.connector.pooling.MySQLConnectionPool", return_value=MagicMock()) as pool:
        database.get_db_connection()
        database.get_db_connection()
    assert pool.call_count == 1
    assert pool.call_args.kwargs["database"] == "appdb"

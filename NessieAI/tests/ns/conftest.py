"""Re-export the shared API fixtures from nextseek_api/conftest.py.

These tests used to live under nextseek_api/, where pytest picked those
fixtures up by directory. Importing them by name keeps them available here.
Never declare pytest_plugins in this file: pytest rejects it outside the
initial conftests.
"""
import os

import pytest

from nextseek_api.conftest import (  # noqa: F401
    admin_client,
    admin_user,
    api_client,
    api_user,
    auth_client,
    factory,
    join_turn_threads_at_teardown,
    mock_assistant_permission,
    mock_seek_auth,
    mock_seek_client,
)


def pytest_collection_modifyitems(config, items):
    """``mysql_lane`` tests need lane M's MySQL (dmac/test_settings_mysql.py); everywhere else they are skipped."""
    if os.environ.get("DJANGO_SETTINGS_MODULE") == "dmac.test_settings_mysql":
        return
    skip = pytest.mark.skip(reason="needs lane_mysql (DJANGO_SETTINGS_MODULE=dmac.test_settings_mysql)")
    for item in items:
        if item.get_closest_marker("mysql_lane"):
            item.add_marker(skip)

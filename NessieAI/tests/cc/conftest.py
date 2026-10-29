"""Re-export the shared API fixtures from nextseek_api/conftest.py.

These tests used to live under nextseek_api/, where pytest picked those
fixtures up by directory. Importing them by name keeps them available here.
Never declare pytest_plugins in this file: pytest rejects it outside the
initial conftests.
"""
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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fake_spawner(request, monkeypatch):
    """Every turn test runs against the stand-in spawner; a test that wants its own start_turn patches it after."""
    from NessieAI.tests.cc import fake_spawner

    if request.module.__name__.endswith("test_cc_spawner_client"):
        return None  # that file drives the real client against a real local server

    return fake_spawner.install(monkeypatch)

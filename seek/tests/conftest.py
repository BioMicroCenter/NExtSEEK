"""Shared fixtures for the SEEK OAuth tests.

The row builders themselves live in ``seek_mirror_rows.py`` rather than here:
conftest fixtures are only visible below the directory they sit in, and tests
outside ``seek/tests/`` need the same rows.
"""

import pytest

from seek.tests.seek_mirror_rows import build_seek_identity, build_seek_user


@pytest.fixture
def make_seek_user():
    return build_seek_user


@pytest.fixture
def seek_person():
    """Factory: a SEEK person who has a user account. Returns the person id."""
    return build_seek_identity


@pytest.fixture
def seek_identity(seek_person):
    """The default SEEK person: id 42, login "researcher"."""
    return seek_person()


@pytest.fixture
def client_fixture(client):
    """pytest-django's test client under a name that does not collide.

    The OAuth test modules import ``seek.oauth.client``, which would otherwise
    shadow the ``client`` fixture at module scope.
    """
    return client

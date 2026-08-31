"""Shared fixtures for the SEEK OAuth tests.

The mirrored Rails tables (``seek/models/seek_mirror.py``) declare almost every
column NOT NULL with ``default=None``, so a partial ``create()`` fails on an
integrity error that has nothing to do with the code under test. The builders
here fill every column, once, rather than in each test file.
"""

import pytest

from seek.models.seek_mirror import People, Users

TS = "2026-01-01T00:00:00Z"


def build_seek_user(login, person_id):
    """A row in SEEK's ``users`` table -- what maps a login to a person id."""
    return Users.objects.create(
        login=login, person_id=person_id, crypted_password="x", salt="x",
        created_at=TS, updated_at=TS, remember_token="x",
        remember_token_expires_at=TS, activation_code="x", activated_at=TS,
        reset_password_code="x", reset_password_code_until=TS, posts_count=0,
        last_seen_at=TS, uuid=f"user-{person_id}-{login}",
    )


def build_seek_person(person_id, first="Ada", last="Lovelace", email="ada@example.org"):
    """A row in SEEK's ``people`` table -- name and contact details."""
    return People.objects.create(
        id=person_id, created_at=TS, updated_at=TS, first_name=first,
        last_name=last, email=email, phone="", skype_name="", web_page="",
        description="", avatar_id=0, status_id=0, first_letter=(first or "?")[:1],
        uuid=f"person-{person_id}", roles_mask=0, orcid="",
    )


@pytest.fixture
def make_seek_user():
    return build_seek_user


@pytest.fixture
def seek_person():
    """Factory: a SEEK person who has a user account. Returns the person id."""

    def _make(person_id=42, login="researcher", first="Ada", last="Lovelace",
              email="ada@example.org"):
        build_seek_person(person_id, first, last, email)
        build_seek_user(login, person_id)
        return person_id

    return _make


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

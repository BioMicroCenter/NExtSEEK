"""Hermetic tests for resolve_user_project with a stubbed SeekDB."""
import pytest

from nextseek_api.cc_assistant.cc_provision import (
    ProjectIdentity,
    ProjectResolutionError,
    resolve_user_project,
)


class _StubSeekDB:
    last_args = None

    def __init__(
        self,
        server,
        username,
        password,
        *,
        membership=None,
        titles=None,
        boom=False,
    ):
        _StubSeekDB.last_args = (server, username, password)
        self._membership = [] if membership is None else membership
        self._titles = titles or {}
        self._boom = boom

    def getCurrentUser(self):
        if self._boom:
            raise RuntimeError("SEEK unreachable")
        return {"data": {"relationships": {"projects": {"data": self._membership}}}}

    def getProjectName(self, projectid):
        return self._titles[str(projectid)]


def _factory(**kwargs):
    def make(server, username, password):
        return _StubSeekDB(server, username, password, **kwargs)

    return make


def test_resolves_first_project_credentialed():
    factory = _factory(
        membership=[{"id": "42"}, {"id": "99"}],
        titles={"42": "Liver Tox (NDMA) study"},
    )

    project = resolve_user_project("alice", "pw", seekdb_factory=factory)

    assert project == ProjectIdentity(
        id="42", title="Liver Tox (NDMA) study", slug="liver-tox-ndma-study"
    )
    assert _StubSeekDB.last_args == (None, "alice", "pw")


def test_empty_membership_falls_back_to_personal_namespace():
    project = resolve_user_project("bob", "pw", seekdb_factory=_factory(membership=[]))

    assert project == ProjectIdentity(id="personal-bob", title="bob", slug="bob")


def test_seek_outage_fails_closed():
    with pytest.raises(ProjectResolutionError):
        resolve_user_project("carol", "pw", seekdb_factory=_factory(boom=True))


def test_malformed_membership_fails_closed():
    with pytest.raises(ProjectResolutionError):
        resolve_user_project("dee", "pw", seekdb_factory=_factory(membership=[{}]))


def test_custom_personal_prefix():
    project = resolve_user_project(
        "dave",
        "pw",
        seekdb_factory=_factory(membership=[]),
        personal_prefix="priv-",
    )

    assert project.id == "priv-dave"

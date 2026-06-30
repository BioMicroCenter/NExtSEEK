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
        current_user=None,
    ):
        _StubSeekDB.last_args = (server, username, password)
        self._membership = [] if membership is None else membership
        self._titles = titles or {}
        self._boom = boom
        self._current_user = current_user

    def getCurrentUser(self):
        if self._boom:
            raise RuntimeError("SEEK unreachable")
        if self._current_user is not None:
            return self._current_user
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


@pytest.mark.parametrize("membership", [None, {}, ""])
def test_malformed_falsy_membership_fails_closed(membership):
    payload = {"data": {"relationships": {"projects": {"data": membership}}}}

    with pytest.raises(ProjectResolutionError):
        resolve_user_project("erin", "pw", seekdb_factory=_factory(current_user=payload))


@pytest.mark.parametrize("membership", [[{"id": ""}], [{"id": None}]])
def test_missing_project_id_fails_closed(membership):
    with pytest.raises(ProjectResolutionError):
        resolve_user_project("fran", "pw", seekdb_factory=_factory(membership=membership))


def test_missing_project_title_fails_closed():
    with pytest.raises(ProjectResolutionError):
        resolve_user_project(
            "gail",
            "pw",
            seekdb_factory=_factory(membership=[{"id": "42"}], titles={"42": ""}),
        )


@pytest.mark.parametrize("project_id", ["../42", "a/b", ".", "..", ""])
def test_malicious_project_id_fails_closed(project_id):
    with pytest.raises(ProjectResolutionError):
        resolve_user_project(
            "hank",
            "pw",
            seekdb_factory=_factory(
                membership=[{"id": project_id}],
                titles={str(project_id): "Bad Project"},
            ),
        )


def test_malicious_personal_namespace_fails_closed():
    with pytest.raises(ValueError):
        resolve_user_project("bad/user", "pw", seekdb_factory=_factory(membership=[])).dirname


def test_custom_personal_prefix():
    project = resolve_user_project(
        "dave",
        "pw",
        seekdb_factory=_factory(membership=[]),
        personal_prefix="priv-",
    )

    assert project.id == "priv-dave"

"""Hermetic tests for resolve_user_project with a stubbed SeekDB."""
import pytest

from NessieAI.cc.cc_provision import (
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

    def getCurrentUser(self, timeout=None):
        self.last_timeout = timeout
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


# P6 T6: the failure names its step and how long it took, and reaches django.log.
def test_a_failure_names_its_step_and_how_long_it_took():
    class _NotJson(_StubSeekDB):
        def getCurrentUser(self, timeout=None):
            raise ValueError("not JSON")

    with pytest.raises(ProjectResolutionError, match=r"^SEEK /people/current failed after \d+\.\ds: ValueError"):
        resolve_user_project("alice", "pw", seekdb_factory=lambda s, u, p: _NotJson(s, u, p))


def test_a_title_failure_names_its_step():
    class _NoTitle(_StubSeekDB):
        def getProjectName(self, projectid):
            raise KeyError(projectid)

    factory = lambda s, u, p: _NoTitle(s, u, p, membership=[{"id": "42"}])  # noqa: E731
    with pytest.raises(ProjectResolutionError, match=r"^SEEK project title failed after \d+\.\ds: KeyError"):
        resolve_user_project("alice", "pw", seekdb_factory=factory)


def test_cc_warnings_reach_django_log():
    from django.conf import settings

    assert settings.LOGGING["loggers"]["NessieAI.cc"]["handlers"] == ["logfile_warnings"]


def test_the_membership_read_is_bounded():
    made = []

    def factory(s, u, p):
        made.append(_StubSeekDB(s, u, p))
        return made[0]

    resolve_user_project("alice", "pw", seekdb_factory=factory)
    assert made[0].last_timeout == (10, 60)


# P6 T8: one retry of the membership read on a transient SEEK failure (dev 11-20: SEEK answered non-JSON while its
# workers recycled). The retry runs the same read and the same checks; a refusal or a malformed answer is not retried.
class _Flaky(_StubSeekDB):
    calls = 0

    def __init__(self, *a, first=None, **k):
        super().__init__(*a, **k)
        self._first = first

    def getCurrentUser(self, timeout=None):
        type(self).calls += 1
        if type(self).calls == 1 and self._first is not None:
            raise self._first
        return super().getCurrentUser(timeout=timeout)


def _not_json(status):
    from seek.seekapi import SeekNotJSON

    err = SeekNotJSON(f"SEEK /people/current answered {status} (text/html), not JSON")
    err.status_code = status
    return err


@pytest.fixture
def no_sleep(monkeypatch):
    waited = []
    monkeypatch.setattr("NessieAI.cc.cc_provision.time.sleep", waited.append)
    _Flaky.calls = 0
    return waited


@pytest.mark.parametrize("first", [__import__("requests").ConnectionError("reset"),
                                   __import__("requests").Timeout("slow"), "502"])
def test_a_transient_failure_is_retried_once(no_sleep, first):
    err = _not_json(502) if first == "502" else first
    factory = lambda s, u, p: _Flaky(s, u, p, first=err, membership=[{"id": "42"}], titles={"42": "Zeta"})  # noqa: E731
    project = resolve_user_project("alice", "pw", seekdb_factory=factory)
    assert project.id == "42" and _Flaky.calls == 2 and no_sleep == [2]


def test_a_second_failure_still_stops_the_turn(no_sleep):
    class _Down(_Flaky):
        def getCurrentUser(self, timeout=None):
            _Flaky.calls += 1
            raise _not_json(503)

    with pytest.raises(ProjectResolutionError, match="SEEK /people/current failed"):
        resolve_user_project("alice", "pw", seekdb_factory=lambda s, u, p: _Down(s, u, p))
    assert _Flaky.calls == 2


@pytest.mark.parametrize("err", [_not_json(401), KeyError("data"), ValueError("not JSON")])
def test_a_refusal_or_a_malformed_answer_is_not_retried(no_sleep, err):
    factory = lambda s, u, p: _Flaky(s, u, p, first=err)  # noqa: E731
    with pytest.raises(ProjectResolutionError):
        resolve_user_project("alice", "pw", seekdb_factory=factory)
    assert _Flaky.calls == 1 and no_sleep == []

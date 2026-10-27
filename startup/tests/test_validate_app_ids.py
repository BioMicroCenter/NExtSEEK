"""validate.check_app_ids: the dedicated app uid/gid 10001 must be free on the host, and the host gid sane."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from startup.steps import validate


@pytest.fixture
def free(monkeypatch: pytest.MonkeyPatch) -> None:
    def getpwuid(u): raise KeyError(u)
    def getgrgid(g): raise KeyError(g)
    monkeypatch.setattr(validate.pwd, "getpwuid", getpwuid)
    monkeypatch.setattr(validate.grp, "getgrgid", getgrgid)
    monkeypatch.setattr(validate.grp, "getgrnam", lambda n: SimpleNamespace(gr_gid=994))


def test_free_ids_pass(free) -> None:
    r = validate.check_app_ids("1000")
    assert r.ok, r.detail


def test_uid_taken_fails_naming_the_account(free, monkeypatch) -> None:
    monkeypatch.setattr(validate.pwd, "getpwuid", lambda u: SimpleNamespace(pw_name="ki-person"))
    r = validate.check_app_ids("1000")
    assert not r.ok and "ki-person" in r.detail


def test_gid_taken_fails(free, monkeypatch) -> None:
    monkeypatch.setattr(validate.grp, "getgrgid", lambda g: SimpleNamespace(gr_name="ki-bmc"))
    r = validate.check_app_ids("1000")
    assert not r.ok and "ki-bmc" in r.detail


@pytest.mark.parametrize("gid", ["", "0", "994", "abc"])
def test_bad_host_gid_fails(free, gid) -> None:
    r = validate.check_app_ids(gid)
    assert not r.ok and "NEXTSEEK_HOST_GID" in r.detail

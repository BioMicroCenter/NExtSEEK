"""SeekAPI.getCurrentUser: a non-JSON answer names its status, and the Container-CC startup can bound the call."""
import pytest
import requests


class _Resp:
    def __init__(self, status=200, body=None, ctype="application/json"):
        self.status_code = status
        self.headers = {"Content-Type": ctype} if ctype else {}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _api():
    from seek.seekapi import SeekAPI

    return SeekAPI("https://seek.example", "alice", "pw")


def test_a_non_json_answer_names_its_status(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(502, None, "text/html"))
    with pytest.raises(ValueError) as e:
        _api().getCurrentUser()
    assert "502" in str(e.value) and "text/html" in str(e.value)
    assert e.value.status_code == 502


def test_the_timeout_reaches_the_request(monkeypatch):
    seen = {}
    monkeypatch.setattr(requests, "get", lambda *a, **k: seen.update(k) or _Resp(200, {"data": {}}))
    _api().getCurrentUser(timeout=(10, 60))
    assert seen["timeout"] == (10, 60)


def test_no_timeout_by_default(monkeypatch):
    seen = {}
    monkeypatch.setattr(requests, "get", lambda *a, **k: seen.update(k) or _Resp(200, {"data": {}}))
    _api().getCurrentUser()
    assert seen.get("timeout") is None


def test_a_json_refusal_is_returned_as_before(monkeypatch):
    refusal = {"errors": [{"title": "Unauthorized"}]}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(401, refusal))
    assert _api().getCurrentUser() == refusal

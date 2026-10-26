import pytest

from NessieAI.ns.paper.fetch import HostNotAllowed, SourceNotFound, SourceUnavailable
from NessieAI.tests.ns.paper.fakes import FakeResponse, fetcher, key

URL = "https://www.ebi.ac.uk/x"


def test_get_returns_body_with_params():
    f, _ = fetcher({key(URL, {"a": 1}): FakeResponse(200, b"ok")})
    assert f.get(URL, params={"a": 1}) == b"ok"


def test_off_list_host_and_plain_http_refused_before_any_request():
    f, session = fetcher({})
    with pytest.raises(HostNotAllowed):
        f.get("https://evil.example/x")
    with pytest.raises(HostNotAllowed):
        f.get("http://www.ebi.ac.uk/x")
    assert session.calls == []


def test_redirect_off_list_refused():
    f, _ = fetcher({key(URL): FakeResponse(302, headers={"Location": "https://evil.example/y"})})
    with pytest.raises(HostNotAllowed):
        f.get(URL)


def test_redirect_on_list_followed():
    f, _ = fetcher({key(URL): FakeResponse(301, headers={"Location": "/y"}),
                    key("https://www.ebi.ac.uk/y"): FakeResponse(200, b"moved")})
    assert f.get(URL) == b"moved"


def test_transient_then_ok():
    f, session = fetcher({key(URL): [FakeResponse(503), FakeResponse(200, b"ok")]})
    assert f.get(URL) == b"ok"
    assert len(session.calls) == 2


def test_transient_exhausted_is_unavailable():
    f, session = fetcher({key(URL): [FakeResponse(429)]}, retries=2)
    with pytest.raises(SourceUnavailable) as err:
        f.get(URL)
    assert err.value.host == "www.ebi.ac.uk"
    assert len(session.calls) == 3


def test_connection_error_is_transient():
    f, _ = fetcher({key(URL): [ConnectionError("boom")]}, retries=1)
    with pytest.raises(SourceUnavailable):
        f.get(URL)


def test_404_is_not_found():
    f, _ = fetcher({})
    with pytest.raises(SourceNotFound) as err:
        f.get(URL)
    assert err.value.status == 404


def test_body_over_cap_is_refused():
    f, _ = fetcher({key(URL): FakeResponse(200, b"x" * 10)}, max_bytes=5)
    with pytest.raises(SourceNotFound) as err:
        f.get(URL)
    assert err.value.status == 413


def test_get_json():
    f, _ = fetcher({key(URL): FakeResponse(200, b'{"a": 1}')})
    assert f.get_json(URL) == {"a": 1}

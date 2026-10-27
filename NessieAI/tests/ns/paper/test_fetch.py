import email.utils

import pytest
import requests

from NessieAI.ns.paper.fetch import HostNotAllowed, SourceNotFound, SourceUnavailable
from NessieAI.tests.ns.paper.fakes import FakeResponse, FakeSession, fetcher, key

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


@pytest.mark.parametrize("exc", [requests.ConnectionError("boom"), requests.Timeout("slow")])
def test_connection_error_is_transient(exc):
    f, _ = fetcher({key(URL): [exc]}, retries=1)
    with pytest.raises(SourceUnavailable):
        f.get(URL)


def test_a_programming_error_in_the_request_is_raised_not_retried():
    f, session = fetcher({key(URL): [TypeError("bad call"), FakeResponse(200, b"ok")]})
    with pytest.raises(TypeError):
        f.get(URL)
    assert len(session.calls) == 1


class _Broken(FakeResponse):
    def __init__(self, exc):
        super().__init__(200)
        self.exc = exc

    def iter_content(self, size):
        yield b"part"
        raise self.exc


def test_a_body_cut_off_mid_stream_is_transient():
    cut = requests.exceptions.ChunkedEncodingError("connection broken")
    f, session = fetcher({key(URL): [_Broken(cut), FakeResponse(200, b"ok")]})
    assert f.get(URL) == b"ok"
    assert len(session.calls) == 2


def test_a_programming_error_in_the_body_read_is_raised_not_retried():
    f, session = fetcher({key(URL): [_Broken(KeyError("oops")), FakeResponse(200, b"ok")]})
    with pytest.raises(KeyError):
        f.get(URL)
    assert len(session.calls) == 1


def test_408_is_retried():
    f, session = fetcher({key(URL): [FakeResponse(408), FakeResponse(200, b"ok")]})
    assert f.get(URL) == b"ok"
    assert len(session.calls) == 2


# Retry-After: honoured, but only inside the retry count and the deadline.

def _sleepy(routes, **kwargs):
    from NessieAI.ns.paper.fetch import Fetcher

    slept, session = [], FakeSession(routes)
    return Fetcher(session, sleep=slept.append, **kwargs), session, slept


def test_retry_after_seconds_is_waited_instead_of_a_shorter_backoff():
    f, _, slept = _sleepy({key(URL): [FakeResponse(429, headers={"Retry-After": "7"}), FakeResponse(200, b"ok")]},
                          backoff_s=2.0)
    assert f.get(URL) == b"ok"
    assert slept == [7.0]


def test_a_shorter_retry_after_never_cuts_the_backoff():
    f, _, slept = _sleepy({key(URL): [FakeResponse(503, headers={"Retry-After": "0"}), FakeResponse(200, b"ok")]},
                          backoff_s=2.0)
    assert f.get(URL) == b"ok"
    assert slept == [2.0]


def test_retry_after_http_date_is_read_against_the_clock():
    now = 1_700_000_000.0
    when = email.utils.formatdate(now + 30, usegmt=True)
    f, _, slept = _sleepy({key(URL): [FakeResponse(503, headers={"Retry-After": when}), FakeResponse(200, b"ok")]},
                          backoff_s=1.0, now=lambda: now)
    assert f.get(URL) == b"ok"
    assert slept == [30.0]


def test_an_unreadable_retry_after_falls_back_to_the_backoff():
    f, _, slept = _sleepy({key(URL): [FakeResponse(429, headers={"Retry-After": "soon"}), FakeResponse(200, b"ok")]},
                          backoff_s=2.0)
    assert f.get(URL) == b"ok"
    assert slept == [2.0]


def test_a_retry_after_past_the_deadline_gives_up_without_sleeping():
    f, session, slept = _sleepy({key(URL): [FakeResponse(429, headers={"Retry-After": "60"}), FakeResponse(200)]},
                                time_left=lambda: 20.0)
    with pytest.raises(SourceUnavailable) as err:
        f.get(URL)
    assert slept == [] and len(session.calls) == 1
    assert "429" in err.value.reason


def test_a_backoff_past_the_deadline_gives_up_without_sleeping():
    f, session, slept = _sleepy({key(URL): [FakeResponse(503), FakeResponse(200)]},
                                backoff_s=5.0, time_left=lambda: 5.5)
    with pytest.raises(SourceUnavailable):
        f.get(URL)
    assert slept == [] and len(session.calls) == 1


def test_a_retry_after_over_the_cap_gives_up_without_sleeping():
    f, session, slept = _sleepy({key(URL): [FakeResponse(503, headers={"Retry-After": "3600"}), FakeResponse(200)]},
                                max_wait_s=60.0)
    with pytest.raises(SourceUnavailable):
        f.get(URL)
    assert slept == [] and len(session.calls) == 1


def test_retry_after_does_not_add_attempts():
    f, session, slept = _sleepy({key(URL): [FakeResponse(429, headers={"Retry-After": "3"})]}, retries=2)
    with pytest.raises(SourceUnavailable):
        f.get(URL)
    assert len(session.calls) == 3 and slept == [3.0, 4.0]


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


# One deadline for every request a Fetcher makes (time_left), not one timeout reused per request.

class _Clock:
    def __init__(self, budget: float):
        self.now, self.budget = 0.0, budget

    def left(self):
        return self.budget - self.now


class _TimedSession:
    """Serves ``responses`` in order; each request costs ``cost`` seconds on the clock."""

    def __init__(self, clock, responses, cost=10.0):
        self.clock, self.responses, self.cost = clock, list(responses), cost
        self.timeouts = []

    def get(self, url, params=None, timeout=None, **kwargs):
        self.timeouts.append(timeout)
        self.clock.now += self.cost
        return self.responses.pop(0)


def _timed(clock, responses, **kwargs):
    from NessieAI.ns.paper.fetch import Fetcher

    session = _TimedSession(clock, responses, **kwargs)
    return Fetcher(session, retries=0, sleep=lambda s: None, time_left=clock.left), session


def test_each_request_waits_only_for_the_time_left():
    clock = _Clock(30.0)
    f, session = _timed(clock, [FakeResponse(200, b"a"), FakeResponse(200, b"b")])
    assert f.get(URL) == b"a" and f.get(URL) == b"b"
    (c1, r1), (c2, r2) = session.timeouts
    assert r1 == 30.0 and r2 == 20.0 and c1 <= r1 and c2 <= r2


def test_redirect_hops_share_the_deadline():
    clock = _Clock(25.0)
    f, session = _timed(clock, [FakeResponse(302, headers={"Location": URL + "/y"}), FakeResponse(200, b"ok")])
    assert f.get(URL) == b"ok"
    assert [t[1] for t in session.timeouts] == [25.0, 15.0]


def test_an_exhausted_budget_raises_out_of_time_before_any_request():
    from NessieAI.ns.paper.fetch import OutOfTime

    for left in (0.5, None):
        f, session = _timed(_Clock(0.0), [FakeResponse(200, b"x")])
        f._time_left = lambda: left
        with pytest.raises(OutOfTime):
            f.get(URL)
        assert session.timeouts == []


def test_a_body_that_crosses_the_deadline_is_out_of_time_and_not_retried():
    from NessieAI.ns.paper.fetch import OutOfTime

    clock = _Clock(12.0)

    class Trickle(FakeResponse):
        def iter_content(self, size):
            for _ in range(5):
                clock.now += 1.0
                yield b"x"

    f, session = _timed(clock, [Trickle(200), FakeResponse(200, b"never")], cost=8.0)
    f._retries = 3
    with pytest.raises(OutOfTime):
        f.get(URL)
    assert len(session.timeouts) == 1


def test_the_allowlist_holds_only_hosts_an_adapter_reads():
    from NessieAI.ns.paper.fetch import ALLOWED_HOSTS

    assert ALLOWED_HOSTS == {"www.ebi.ac.uk", "www.ncbi.nlm.nih.gov", "zenodo.org", "api.figshare.com",
                             "purl.stanford.edu"}

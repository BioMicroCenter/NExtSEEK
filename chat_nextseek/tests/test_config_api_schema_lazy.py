"""Lazy API_SCHEMA (review follow-up FU6, 2026-07-07).

The remote schema fetch is a REST self-call. Fetched eagerly at ChatConfig
construction, it serialized every gunicorn worker's cold boot: the master
binds :8000 pre-fork, all workers import local_settings in parallel, each
connect lands in the accept backlog nobody can serve yet, and each worker
burned the full read timeout (~20s) before the fail-soft branch left
API_SCHEMA empty for the process lifetime.

Now: API_SCHEMA is a lazy property — no fetch at construction, fetch on
first access with ONE retry when the first attempt came back empty, then
cached (even when empty) so a dead backend is never hammered. The fetch
timeout is capped at (2, 5): connect succeeds against a bound-but-unserved
listener, so the READ timeout is the only effective lever.
"""
from __future__ import annotations

import pytest

from chat_nextseek.config import ChatConfig


def _bare_config(monkeypatch, fetch_results: list[dict]):
    """A ChatConfig shell with the real lazy-property machinery and a
    counting fake fetch (full __init__ needs DB/network)."""
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._init_api_schema_state()
    calls: list[int] = []

    def fake_fetch(self):
        calls.append(1)
        return fetch_results[min(len(calls) - 1, len(fetch_results) - 1)]

    monkeypatch.setattr(ChatConfig, "_load_api_schema_from_remote", fake_fetch)
    return cfg, calls


class TestLazyFetch:
    def test_no_fetch_until_first_access(self, monkeypatch):
        cfg, calls = _bare_config(monkeypatch, [{"ep": {}}])
        assert calls == []
        assert cfg.API_SCHEMA == {"ep": {}}
        assert calls == [1]

    def test_successful_fetch_cached_forever(self, monkeypatch):
        cfg, calls = _bare_config(monkeypatch, [{"ep": {}}])
        for _ in range(3):
            assert cfg.API_SCHEMA == {"ep": {}}
        assert len(calls) == 1

    def test_empty_fetch_retried_exactly_once(self, monkeypatch):
        cfg, calls = _bare_config(monkeypatch, [{}, {}, {"never": {}}])
        assert cfg.API_SCHEMA == {}  # attempt 1
        assert cfg.API_SCHEMA == {}  # attempt 2 (the one-shot retry)
        assert cfg.API_SCHEMA == {}  # cached — no further attempts
        assert len(calls) == 2

    def test_retry_can_recover_from_cold_boot_miss(self, monkeypatch):
        """The pre-fix defect: a failed boot-time fetch left API_SCHEMA empty
        for the process lifetime. The retry heals it once the listener is up."""
        cfg, calls = _bare_config(monkeypatch, [{}, {"ep": {}}])
        assert cfg.API_SCHEMA == {}
        assert cfg.API_SCHEMA == {"ep": {}}
        assert cfg.API_SCHEMA == {"ep": {}}
        assert len(calls) == 2


class TestExplicitAssignment:
    def test_setter_short_circuits_fetching(self, monkeypatch):
        cfg, calls = _bare_config(monkeypatch, [{"remote": {}}])
        cfg.API_SCHEMA = {"pinned": {}}
        assert cfg.API_SCHEMA == {"pinned": {}}
        assert calls == []

    def test_config_map_can_still_provide_api_schema(self, monkeypatch):
        """_load_config_map does plain setattr for arbitrary keys — the
        property must accept assignment or config_map would break."""
        cfg, calls = _bare_config(monkeypatch, [{"remote": {}}])
        cfg._load_config_map({"API_SCHEMA": {"from_map": {}}})
        assert cfg.API_SCHEMA == {"from_map": {}}
        assert calls == []


class TestCacheSurvivesPerRequestCopies:
    """Cold-review finding (2026-07-08, major): the orchestrator and granular
    ops copy.copy() the config per request; a per-instance cache would land
    on the copy and die with the request, so the singleton re-fetched on
    EVERY request forever. The cache must be shared across shallow copies."""

    def test_fetch_on_copy_populates_the_original(self, monkeypatch):
        import copy

        cfg, calls = _bare_config(monkeypatch, [{"ep": {}}])
        request_copy = copy.copy(cfg)
        assert request_copy.API_SCHEMA == {"ep": {}}
        # ... the copy is discarded; the ORIGINAL must now be warm:
        assert cfg.API_SCHEMA == {"ep": {}}
        assert len(calls) == 1

    def test_attempt_cap_shared_across_copies(self, monkeypatch):
        import copy

        cfg, calls = _bare_config(monkeypatch, [{}, {}, {}])
        for _ in range(3):
            assert copy.copy(cfg).API_SCHEMA == {}
        assert len(calls) == 2  # cap binds across request copies

    def test_explicit_assignment_on_copy_stays_local(self, monkeypatch):
        """An explicit override on a per-request copy must not clobber the
        process singleton."""
        import copy

        cfg, calls = _bare_config(monkeypatch, [{"remote": {}}])
        request_copy = copy.copy(cfg)
        request_copy.API_SCHEMA = {"pinned": {}}
        assert request_copy.API_SCHEMA == {"pinned": {}}
        assert cfg.API_SCHEMA == {"remote": {}}  # singleton unaffected


class TestRaisingFetch:
    """Cold-review finding (2026-07-08): the attempt cap must bind — and no
    exception may propagate into a request handler — even when the loader
    RAISES instead of returning."""

    def _raising_config(self, monkeypatch):
        cfg = ChatConfig.__new__(ChatConfig)
        cfg._init_api_schema_state()
        calls: list[int] = []

        def raising_fetch(self):
            calls.append(1)
            raise RuntimeError("boom")

        monkeypatch.setattr(
            ChatConfig, "_load_api_schema_from_remote", raising_fetch
        )
        return cfg, calls

    def test_raise_is_fail_soft_and_capped(self, monkeypatch):
        cfg, calls = self._raising_config(monkeypatch)
        for _ in range(4):
            assert cfg.API_SCHEMA == {}  # never raises into the caller
        assert len(calls) == 2  # cap still binds

    def test_non_dict_yaml_spec_fail_soft(self, monkeypatch):
        """A misrouted 200 (HTML/maintenance page parsing to a YAML scalar)
        must yield {} — not AttributeError mid-request."""
        pytest.importorskip("yaml")
        import chat_nextseek.config as config_module

        class _Resp:
            status_code = 200
            text = "just a maintenance banner"

        monkeypatch.setattr(
            config_module.requests, "get", lambda url, **kw: _Resp()
        )
        cfg = ChatConfig.__new__(ChatConfig)
        cfg._init_api_schema_state()
        cfg.NEXTSEEK_BASE_URL = "http://127.0.0.1:9"
        assert cfg._load_api_schema_from_remote() == {}


class TestFetchTimeout:
    def test_read_timeout_capped(self, monkeypatch):
        """Connect succeeds against gunicorn's bound-but-unserved socket, so
        only the read timeout bounds the cold-boot stall — it must be short."""
        import chat_nextseek.config as config_module

        captured: dict = {}

        def fake_get(url, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            raise config_module.requests.exceptions.ConnectionError("boom")

        monkeypatch.setattr(config_module.requests, "get", fake_get)
        cfg = ChatConfig.__new__(ChatConfig)
        cfg._init_api_schema_state()
        cfg.NEXTSEEK_BASE_URL = "http://127.0.0.1:9"
        assert cfg._load_api_schema_from_remote() == {}  # fail-soft
        connect_read = captured["timeout"]
        assert isinstance(connect_read, tuple)
        assert connect_read[1] <= 5


class TestConcurrentAccess:
    def test_cold_burst_fetches_at_most_twice(self, monkeypatch):
        import threading
        import time

        cfg, calls = _bare_config(monkeypatch, [{"ep": {}}])
        orig = ChatConfig._load_api_schema_from_remote

        def slow(self):
            time.sleep(0.05)
            return orig(self)

        monkeypatch.setattr(ChatConfig, "_load_api_schema_from_remote", slow)
        threads = [threading.Thread(target=lambda: cfg.API_SCHEMA) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(calls) <= cfg._API_SCHEMA_MAX_ATTEMPTS

    def test_late_failure_does_not_clobber_earlier_success(self, monkeypatch):
        # A late-landing FAILED fetch must never overwrite a good schema a
        # concurrent thread already stored. That clobber only exists when BOTH
        # threads pass the cold (`schema is None`) check before either stores —
        # exactly the window the double-checked lock closes. The earlier version
        # let one thread fully store before the other even checked, so the second
        # short-circuited on the cached value and the failing fetch never ran; it
        # passed WITH OR WITHOUT the lock (verified 2026-07-08 against the pre-fix
        # getter). This version pins the interleave: the failing fetch is entered
        # first (past the cold check) and released only AFTER the good schema is
        # stored, so it lands last. Without the lock the unconditional store
        # clobbers -> FAIL; with it the second thread re-checks under the lock and
        # returns the good value.
        import threading
        import time

        good = {"ep": {"ok": True}}
        cfg, _ = _bare_config(monkeypatch, [good, {}])
        bad_in_fetch = threading.Event()   # failing fetch has passed the cold check
        release_bad = threading.Event()    # let the failing fetch return (last)

        def fetch(self):
            if threading.current_thread().name == "bad":
                bad_in_fetch.set()
                release_bad.wait(timeout=5)
                return {}
            return good

        monkeypatch.setattr(ChatConfig, "_load_api_schema_from_remote", fetch)

        bad = threading.Thread(target=lambda: cfg.API_SCHEMA, name="bad")
        winner = threading.Thread(target=lambda: cfg.API_SCHEMA, name="good")
        bad.start()
        assert bad_in_fetch.wait(timeout=5)  # bad is past the cold check, inside fetch
        winner.start()
        # Release the failing fetch only once the good schema is visible (unfixed
        # path stores it immediately) or after a short grace (fixed path: the
        # winner is blocked on the lock the bad thread holds and cannot store
        # until it is released — so the good store necessarily lands afterward).
        deadline = time.monotonic() + 0.5
        while cfg._api_schema_cell["schema"] != good and time.monotonic() < deadline:
            time.sleep(0.005)
        release_bad.set()
        bad.join(timeout=5)
        winner.join(timeout=5)
        assert cfg.API_SCHEMA == good  # the late failure must NOT have clobbered it


"""The one way paper reingest reaches the internet (spec §5): an allowlisted HTTPS GET.

Only hosts in ALLOWED_HOSTS are fetched. Redirects are followed by hand, so one that
leaves the list is refused. A body over max_bytes is refused. A 5xx, 429, timeout or
dropped connection is retried with backoff, then raised as SourceUnavailable, which the
run turns into `paused` (spec §10). Any other 4xx is SourceNotFound.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

ALLOWED_HOSTS = frozenset({
    "www.ebi.ac.uk",          # Europe PMC, the ENA portal API, the PRIDE archive API
    "www.ncbi.nlm.nih.gov",   # GEO
    "api.crossref.org",
    "zenodo.org",
    "api.figshare.com",
    "purl.stanford.edu",      # Stanford Digital Repository metadata
})

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_HOPS = 5


class HostNotAllowed(ValueError):
    pass


class SourceNotFound(Exception):
    def __init__(self, url: str, status: int):
        super().__init__(f"HTTP {status} from {url}")
        self.url, self.status = url, status


class SourceUnavailable(Exception):
    def __init__(self, host: str, reason: str):
        super().__init__(f"{host}: {reason}")
        self.host, self.reason = host, reason


class OutOfTime(Exception):
    """The caller's deadline (``time_left``) ran out. Never retried, never read as SourceUnavailable."""


#: Below this many seconds left no request starts and no body read goes on.
MIN_LEFT_S = 1.0


class _Transient(Exception):
    pass


class Fetcher:
    def __init__(self, session=None, *, allowed=ALLOWED_HOSTS, retries: int = 3, backoff_s: float = 2.0,
                 timeout=(5.0, 60.0), max_bytes: int = 200 * 1024 * 1024,
                 sleep: Callable[[float], None] = time.sleep,
                 time_left: Callable[[], float | None] | None = None):
        """``time_left``, when given, is one deadline for every request this Fetcher makes: it returns the
        seconds left (None meaning none), each request and redirect hop waits at most that long, a body read
        stops when it runs out, and OutOfTime is raised below MIN_LEFT_S. Without it, ``timeout`` applies to
        each request on its own."""
        if session is None:
            import requests

            session = requests.Session()
        self._session, self._allowed = session, allowed
        self._retries, self._backoff, self._timeout = retries, backoff_s, timeout
        self._max_bytes, self._sleep, self._time_left = max_bytes, sleep, time_left

    def _check_time(self) -> float | None:
        """Seconds left under the deadline, or None when there is no deadline. Raises OutOfTime."""
        if self._time_left is None:
            return None
        left = self._time_left()
        if left is None or left < MIN_LEFT_S:
            raise OutOfTime("the time for this step ran out")
        return left

    def _request_timeout(self):
        left = self._check_time()
        if left is None:
            return self._timeout
        connect = self._timeout[0] if isinstance(self._timeout, tuple) else self._timeout
        return (min(connect, left), left)

    def _host(self, url: str) -> str:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in self._allowed:
            raise HostNotAllowed(url)
        return parts.hostname

    def get(self, url: str, *, params: dict | None = None) -> bytes:
        host = self._host(url)
        reason = "no attempt"
        for attempt in range(self._retries + 1):
            if attempt:
                self._sleep(self._backoff * (2 ** (attempt - 1)))
            try:
                return self._follow(url, params)
            except _Transient as exc:
                reason = str(exc)
        raise SourceUnavailable(host, reason)

    def get_text(self, url: str, *, params: dict | None = None) -> str:
        return self.get(url, params=params).decode("utf-8", errors="replace")

    def get_json(self, url: str, *, params: dict | None = None) -> Any:
        return json.loads(self.get(url, params=params))

    def _follow(self, url: str, params: dict | None) -> bytes:
        for _ in range(_MAX_HOPS + 1):
            self._host(url)
            timeout = self._request_timeout()
            try:
                resp = self._session.get(url, params=params, timeout=timeout,
                                         stream=True, allow_redirects=False)
            except Exception as exc:  # requests' ConnectionError, Timeout and kin
                raise _Transient(type(exc).__name__) from exc
            try:
                if resp.status_code in _REDIRECTS:
                    url, params = urljoin(url, resp.headers.get("Location", "")), None
                    continue
                if resp.status_code in _RETRY_STATUS:
                    raise _Transient(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise SourceNotFound(url, resp.status_code)
                return self._read(resp, url)
            finally:
                resp.close()
        raise SourceNotFound(url, 310)

    def _read(self, resp, url: str) -> bytes:
        chunks, total = [], 0
        try:
            for chunk in resp.iter_content(64 * 1024):
                self._check_time()
                total += len(chunk)
                if total > self._max_bytes:
                    raise SourceNotFound(url, 413)
                chunks.append(chunk)
        except (SourceNotFound, OutOfTime):
            raise
        except Exception as exc:  # a body cut off mid-stream
            raise _Transient(type(exc).__name__) from exc
        return b"".join(chunks)

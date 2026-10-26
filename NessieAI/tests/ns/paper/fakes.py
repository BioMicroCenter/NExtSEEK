"""Offline stand-ins for requests.Session, so no engine test touches the network."""
from __future__ import annotations

import json
from urllib.parse import urlencode


def key(url: str, params: dict | None = None) -> str:
    """The route key a request is looked up by: the URL plus its sorted query."""
    if not params:
        return url
    return url + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))


class FakeResponse:
    def __init__(self, status: int = 200, body: bytes | str = b"", headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body.encode() if isinstance(body, str) else body

    def iter_content(self, size: int):
        for i in range(0, len(self._body), size):
            yield self._body[i:i + size]

    def close(self) -> None:
        pass


def js(obj) -> FakeResponse:
    return FakeResponse(200, json.dumps(obj))


class FakeSession:
    """routes: key -> FakeResponse | Exception | list of those (served in order, the last repeating)."""

    def __init__(self, routes: dict):
        self.routes = {k: (list(v) if isinstance(v, list) else v) for k, v in routes.items()}
        self.calls: list[str] = []

    def get(self, url, params=None, **kwargs):
        k = key(url, params)
        self.calls.append(k)
        answer = self.routes.get(k, FakeResponse(404))
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def fetcher(routes: dict, **kwargs):
    from NessieAI.ns.paper.fetch import Fetcher

    session = FakeSession(routes)
    return Fetcher(session, sleep=lambda seconds: None, **kwargs), session

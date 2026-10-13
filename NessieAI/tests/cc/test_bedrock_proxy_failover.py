"""The Bedrock proxy relays the two failures Claude Code never falls back on as ones it does (D6, D7, 2026-09-28).

Claude Code 2.1.282 switches to its ``--fallback-model`` on the first 5xx (a 504 from this proxy included), but never
on a hang or a 429 (agent A's fake-Bedrock runs of 2026-09-25). So a hung Opus 4.8 ran the CC turn out at 180 s
without asking Opus 4.7, and a throttled one ended the turn in about 4 s. The relay now:

* answers 504 ``{"error": "upstream timeout"}`` when a STREAMED invoke has no response headers within
  ``stream_headers_timeout`` (45 s, below Claude Code's own 60 s per request), and leaves a plain ``/invoke`` on
  the 600 s read timeout (its headers come only with the whole answer);
* relays an upstream 429 as a 503 with the same body and ``x-nextseek-upstream-status: 429``;
* logs how long each request waited for its headers, and the upstream status it mapped.

The proxy is a FastAPI app in a hyphenated directory, so it is loaded here as the package ``app`` it imports itself
as, the way its image runs it; its upstream is an httpx MockTransport. FastAPI is not in the app image or in any CI
lane, so this file runs in a host lane, from the repo root, without the Django conftest of this directory:
``uv run --no-project --with pytest --with fastapi --with httpx python -m pytest --noconftest -o addopts=""
NessieAI/tests/cc/test_bedrock_proxy_failover.py``. No network, no Bedrock.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import sys
import types

import pytest

pytest.importorskip("fastapi")
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from NessieAI import paths  # noqa: E402

APP_DIR = paths.BEDROCK_PROXY_DIR / "app"
STREAM = "/model/us.anthropic.claude-opus-4-8/invoke-with-response-stream"
INVOKE = "/model/us.anthropic.claude-opus-4-8/invoke"


@pytest.fixture
def proxy(monkeypatch):
    """The relay module, imported as ``app.proxy`` from the proxy's own directory, with a token."""
    pkg = types.ModuleType("app")
    pkg.__path__ = [str(APP_DIR)]
    monkeypatch.setitem(sys.modules, "app", pkg)
    modules = {}
    for name in ("config", "proxy"):
        spec = importlib.util.spec_from_file_location(f"app.{name}", APP_DIR / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, f"app.{name}", module)
        spec.loader.exec_module(module)
        modules[name] = module
    mod = modules["proxy"]
    mod.config = modules["config"].ProxyConfig(region="us-east-1", token="test-token", stream_headers_timeout=0.3)
    return mod


def _resp(status, body, headers=None):
    """An upstream answer the relay can stream, as a real one is (``content=`` would already be read)."""
    if not isinstance(body, bytes):
        body = json.dumps(body).encode()
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers or {})


def _upstream(proxy, handler):
    proxy._client = httpx.AsyncClient(base_url="https://bedrock.test", transport=httpx.MockTransport(handler),
                                      timeout=proxy.config.timeout)
    return TestClient(proxy.app)


async def _hang(request):
    await asyncio.sleep(5)
    return _resp(200, b"late")


def test_a_streamed_invoke_with_no_headers_in_time_is_a_504(proxy, caplog):
    client = _upstream(proxy, _hang)
    with caplog.at_level(logging.INFO, logger="bedrock_proxy.access"):
        resp = client.post(STREAM, content=b"{}")
    assert resp.status_code == 504
    assert resp.json() == {"error": "upstream timeout"}
    assert any("-> 504 headers=" in r.getMessage() for r in caplog.records)


def test_the_header_deadline_is_45_s_by_default_below_claude_codes_60_s(proxy):
    from app.config import ProxyConfig

    assert ProxyConfig(region="us-east-1", token="t").stream_headers_timeout == 45.0
    assert ProxyConfig(region="us-east-1", token="t").read_timeout == 600.0


def test_a_plain_invoke_keeps_the_long_read_timeout(proxy):
    """Its headers come only with the whole answer, so the header deadline must not cut it."""
    async def slow_answer(request):
        await asyncio.sleep(0.6)
        return _resp(200, {"content": [{"type": "text", "text": "ok"}]})

    resp = _upstream(proxy, slow_answer).post(INVOKE, content=b"{}")
    assert resp.status_code == 200
    assert resp.json()["content"][0]["text"] == "ok"


def test_a_stream_whose_headers_arrive_in_time_is_relayed_unchanged(proxy):
    async def ok(request):
        return _resp(200, b"event-stream-bytes", {"content-type": "application/vnd.amazon.eventstream"})

    resp = _upstream(proxy, ok).post(STREAM, content=b"{}")
    assert resp.status_code == 200
    assert resp.content == b"event-stream-bytes"
    assert "x-nextseek-upstream-status" not in resp.headers


@pytest.mark.parametrize("path", [STREAM, INVOKE])
def test_an_upstream_429_is_relayed_as_a_503_that_says_so(proxy, caplog, path):
    body = b'{"message":"Too many requests, please wait before trying again."}'

    async def throttled(request):
        return _resp(429, body, {
            "content-type": "application/json",
            "x-amzn-ErrorType": "ThrottlingException:http://internal.amazon.com/coral/com.amazon.bedrock/"})

    with caplog.at_level(logging.INFO, logger="bedrock_proxy.access"):
        resp = _upstream(proxy, throttled).post(path, content=b"{}")
    assert resp.status_code == 503
    assert resp.content == body, "the body is Bedrock's own"
    assert resp.headers["x-nextseek-upstream-status"] == "429"
    # The rest of Bedrock's answer is relayed as it is: Claude Code 2.1.282 falls back on the 503 with the
    # throttle's error type still attached (a free local run through this relay, 2026-09-28).
    assert resp.headers["x-amzn-errortype"].startswith("ThrottlingException")
    assert any("-> 503" in r.getMessage() and "upstream=429" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("status", [200, 400, 403, 500, 503])
def test_every_other_upstream_status_is_relayed_as_it_is(proxy, status):
    async def answer(request):
        return _resp(status, {"message": "m"}, {"x-amzn-ErrorType": "SomeException"})

    resp = _upstream(proxy, answer).post(STREAM, content=b"{}")
    assert resp.status_code == status
    assert "x-nextseek-upstream-status" not in resp.headers


def test_the_access_line_carries_no_request_data(proxy, caplog):
    """The two new fields are numbers the relay measured; the token and the body never reach the log."""
    async def ok(request):
        return _resp(200, b"x")

    with caplog.at_level(logging.INFO, logger="bedrock_proxy.access"):
        _upstream(proxy, ok).post(STREAM, content=b'{"secret-body": 1}')
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "test-token" not in text and "secret-body" not in text
    assert f"POST {STREAM} -> 200 headers=" in text

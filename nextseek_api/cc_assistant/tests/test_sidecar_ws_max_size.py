"""Sidecar WebSocket max_size symmetry (T6 blocker, 2026-07-06).

The sidecar server accepts 16 MiB frames (ns-sidecar/app/server.py), but the
agent's client (_sidecar_client._connect) omitted max_size and so defaulted to
the websockets library's 1 MiB limit. A large op RESPONSE (e.g. an api-read
returning all NHP samples) exceeded the client's 1 MiB recv cap -> the client
closed with WS 1009 "message too big" -> the agent saw
`TRANSPORT_ERROR: ConnectionClosedError`. Narrow queries stayed under 1 MiB and
masked it.

Pin the symmetry invariant: server and client MUST configure the same
WebSocket max_size, so raising one can never silently strand the other.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SERVER = _ROOT / "docker" / "ns-sidecar" / "app" / "server.py"
_CLIENT = (
    _ROOT / "docker" / "cc-runtime" / "build_context" / "plugins"
    / "nextseek" / "bin" / "_sidecar_client.py"
)
_EXPECTED = 16 * 1024 * 1024


def _extract_max_size(path: Path) -> int | None:
    text = path.read_text(encoding="utf-8")
    m = re.search(r"max_size\s*=\s*([0-9][0-9_ *]*[0-9]|[0-9]+)", text)
    if not m:
        return None
    expr = m.group(1)
    if not re.fullmatch(r"[0-9_ *]+", expr):  # arithmetic of literals only
        return None
    return eval(expr, {"__builtins__": {}}, {})  # noqa: S307 — digits/*/_ only


def test_server_sets_16mib_max_size():
    assert _extract_max_size(_SERVER) == _EXPECTED


def test_client_sets_matching_max_size():
    assert _extract_max_size(_CLIENT) == _EXPECTED, (
        "_sidecar_client._connect must set max_size to match the server "
        "(ns-sidecar server.py) or large op responses trip WS 1009 message-too-big"
    )


def test_server_and_client_max_size_are_equal():
    assert _extract_max_size(_SERVER) == _extract_max_size(_CLIENT)

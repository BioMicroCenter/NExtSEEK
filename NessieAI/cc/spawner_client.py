"""The app's client for cc-spawner, the one service that holds the Docker socket (W9).

Container-CC turns and SEEK ops (``seek_rails_runner``) both go through the spawner; the app has no other road to
Docker. ``NEXTSEEK_SPAWNER_URL`` unset or empty means no spawner: every call here fails closed (``health`` says
CC is unavailable, the others raise ``SpawnerUnavailable``). The token goes in ``X-Spawner-Token`` from
``NEXTSEEK_SPAWNER_TOKEN``. Everything is read from the environment at call time.
"""
from __future__ import annotations

import os
from typing import Any

import requests

TOKEN_HEADER = "X-Spawner-Token"
_CONNECT_TIMEOUT_S = 5
_HEALTH_TIMEOUT_S = 10
_STOP_TIMEOUT_S = 30


class SpawnerUnavailable(Exception):
    """The spawner could not be reached, or answered 5xx."""


class SpawnerRefused(Exception):
    """The spawner answered 4xx: it will not carry the request out."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"spawner refused ({status}): {detail}")
        self.status, self.detail = status, detail


def base_url() -> str:
    return (os.environ.get("NEXTSEEK_SPAWNER_URL") or "").strip().rstrip("/")


def enabled() -> bool:
    return bool(base_url())


def _headers() -> dict[str, str]:
    return {TOKEN_HEADER: os.environ.get("NEXTSEEK_SPAWNER_TOKEN", "")}


def _error(resp: requests.Response) -> Exception:
    try:
        detail = str(resp.json().get("error") or resp.text[:200])
    except Exception:  # noqa: BLE001
        detail = resp.text[:200]
    if resp.status_code >= 500:
        return SpawnerUnavailable(f"spawner answered {resp.status_code}: {detail}")
    return SpawnerRefused(resp.status_code, detail)


def _post(path: str, body: dict | None, *, timeout: float, stream: bool = False) -> requests.Response:
    if not enabled():
        raise SpawnerUnavailable("NEXTSEEK_SPAWNER_URL is not set")
    try:
        resp = requests.post(base_url() + path, json=body if body is not None else {}, headers=_headers(),
                             timeout=(_CONNECT_TIMEOUT_S, timeout), stream=stream)
    except requests.RequestException as exc:
        raise SpawnerUnavailable(f"cc-spawner unreachable: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        err = _error(resp)
        resp.close()
        raise err
    return resp


def health() -> tuple[bool, str]:
    """(ok, detail) from ``GET /health`` with the token: the spawner's Docker, image and network checks."""
    if not enabled():
        return False, "CC unavailable: NEXTSEEK_SPAWNER_URL is not set"
    try:
        resp = requests.get(base_url() + "/health", headers=_headers(), timeout=_HEALTH_TIMEOUT_S)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"cc-spawner unreachable: {type(exc).__name__}"
    if resp.status_code != 200:
        return False, f"cc-spawner answered {resp.status_code}"
    return bool(data.get("ok")), str(data.get("detail") or ("ok" if data.get("ok") else "not ready"))


class TurnStream:
    """The spawner's chunked stdout, read a line at a time like ``BridgeAttachSocket.read_event_line``."""

    def __init__(self, resp: requests.Response):
        self._resp = resp
        self._lines = resp.iter_lines(chunk_size=None)

    def read_event_line(self) -> str | None:
        try:
            raw = next(self._lines)
        except StopIteration:
            return None
        except requests.RequestException:
            return None
        return raw.decode("utf-8", errors="replace")

    def close(self) -> None:
        self._resp.close()


def start_turn(body: dict[str, Any], *, read_timeout: float) -> TurnStream:
    """``POST /turns``: returns once the container is running and its stdin is written and closed."""
    return TurnStream(_post("/turns", body, timeout=read_timeout, stream=True))


def stop_turn(run_id: str) -> bool:
    """``POST /turns/{run_id}/stop``: True when the container is confirmed gone."""
    resp = _post(f"/turns/{run_id}/stop", None, timeout=_STOP_TIMEOUT_S)
    return bool(resp.json().get("gone"))


def seek_op(op: str, payload: dict[str, Any] | None, *, timeout: float) -> dict[str, Any]:
    """``POST /seek/{op}``: ``{exit_code, last_line, stderr}`` of the fixed script."""
    return _post(f"/seek/{op}", payload or {}, timeout=timeout).json()

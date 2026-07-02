"""Semantic healthcheck (spec §11, T16): config loads, NEXTSEEK_BASE_URL reachable.

Reachability is confirmed by GETting /nextseek_api/assistant/me/ — this endpoint
requires authentication, so a 401 response is the expected healthy signal (the endpoint
is up and responding; we just don't send credentials). A connection error (httpx.HTTPError)
or any non-401/non-200 unexpected failure indicates an unhealthy state.

Read-only by construction: only a GET is issued, no mutations.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from sidecar.app.config import SidecarConfig
        cfg = SidecarConfig.from_env()

        import httpx
        url = f"{cfg.nextseek_base_url}/nextseek_api/assistant/me/"
        try:
            resp = httpx.get(url, timeout=5.0)
        except httpx.HTTPError as exc:
            print(f"unhealthy: NExtSEEK unreachable at {url}: {type(exc).__name__}", file=sys.stderr)
            return 1

        # 401 = endpoint is up, we just didn't supply credentials (expected healthy signal)
        # 200 = also fine (e.g. some environments allow unauthenticated health endpoints)
        if resp.status_code in (200, 401):
            return 0

        print(f"unhealthy: NExtSEEK /me/ returned HTTP {resp.status_code}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — healthcheck must never crash the prober
        print(f"unhealthy: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""Semantic healthcheck (spec §11, T16): config loads, NEXTSEEK_BASE_URL reachable.

Reachability is confirmed by GETting /docs/, a public page: the sidecar holds no credentials, so
a page behind a login would answer 401 on every probe. A connection error (httpx.HTTPError) or
anything but a 200 indicates an unhealthy state.

Read-only by construction: only a GET is issued, no mutations.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from sidecar.app.config import SidecarConfig
        cfg = SidecarConfig.from_env()

        import httpx
        url = f"{cfg.nextseek_base_url}/docs/"
        try:
            resp = httpx.get(url, timeout=5.0)
        except httpx.HTTPError as exc:
            print(f"unhealthy: NExtSEEK unreachable at {url}: {type(exc).__name__}", file=sys.stderr)
            return 1

        if resp.status_code == 200:
            return 0

        print(f"unhealthy: NExtSEEK /docs/ returned HTTP {resp.status_code}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — healthcheck must never crash the prober
        print(f"unhealthy: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

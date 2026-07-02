"""Sidecar configuration (T16, DD-A5-7). Simplified: holds NEXTSEEK_BASE_URL + staging dir.

The shared backend creds (GCP/Neo4j/MySQL) remain in the process env for NExtSEEK
to use server-side; the sidecar no longer needs SESSION_DB_* (sessions removed T16)
or READ_SAFE_ENDPOINTS_PATH (allowlist gate retired to NExtSEEK T16). Per-request
user creds travel as Basic auth args, never via os.environ (DD-A5-6).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class SidecarConfigError(RuntimeError):
    """Maps to error code CONFIG_MISSING / exit 2 at the protocol layer."""


_REQUIRED = (
    "NEXTSEEK_BASE_URL",
    "SIDECAR_STAGING_DIR",
)


@dataclass(frozen=True)
class SidecarConfig:
    nextseek_base_url: str
    staging_dir: str
    ws_port: int = 8765

    def __repr__(self) -> str:
        return (
            f"SidecarConfig(nextseek_base_url={self.nextseek_base_url!r}, "
            f"staging_dir={self.staging_dir!r}, ws_port={self.ws_port})"
        )

    @classmethod
    def from_env(cls) -> "SidecarConfig":
        missing = [k for k in _REQUIRED if not os.environ.get(k)]
        if missing:
            raise SidecarConfigError(f"missing required env: {missing}")
        return cls(
            nextseek_base_url=os.environ["NEXTSEEK_BASE_URL"],
            staging_dir=os.environ["SIDECAR_STAGING_DIR"],
            ws_port=int(os.environ.get("SIDECAR_WS_PORT", "8765")),
        )
